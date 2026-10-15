`timescale 1ns/1ps
`default_nettype none

// Pgate memory ports through asynchronous mailboxes to the Pocket SDRAM pins.
// This is a component, not an APF top. clk_core is supplied by the future CPU
// clock generator. Assert reset_async for a reset of either clock domain.
module psx_pgate_memory #(parameter integer T_INIT = 12600, T_REFI = 480) (
    input wire clk_74a, clk_core, reset_async,
    output wire core_reset, initialized, protocol_error,
    input wire wram_read, wram_write, bios_read,
    input wire [20:0] a,
    input wire [4:0] bytes,
    input wire [31:0] dout,
    output wire wram_ack, bios_ack, wram_error, bios_error,
    output wire [127:0] din,
    input wire vram_read, vram_write, vram_flush,
    input wire [18:0] vram_addr,
    input wire [2:0] vram_halfwords,
    input wire [15:0] vram_dout,
    output wire vram_read_ack, vram_write_ack, vram_read_error, vram_write_error,
    output wire [63:0] vram_din,
    output wire [12:0] dram_a,
    output wire [1:0] dram_ba, dram_dqm,
    output wire dram_ras_n, dram_cas_n, dram_we_n, dram_cke,
    inout wire [15:0] dram_dq,
    output wire dram_clk
);
    wire mem_clk, mem_reset, mem_initialized, arb_error, adapter_error;
    wire [1:0] s_valid, s_write, s_ready, s_rsp_valid, s_rsp_ready, s_rsp_error;
    wire [1:0] p_valid, p_write, p_ready, p_rsp_valid, p_rsp_ready, p_rsp_error;
    wire [1:0][24:0] s_addr, p_addr;
    wire [1:0][3:0] s_halfwords, p_halfwords;
    wire [1:0][127:0] s_wdata, s_rsp_rdata, p_wdata, p_rsp_rdata;
    wire [1:0][15:0] s_byte_en, p_byte_en;
    wire [1:0] source_resets;
    (* async_reg = "true" *) reg [1:0] initialized_sync;
    (* async_reg = "true" *) reg [1:0] arb_error_sync;
    assign core_reset = source_resets[0] || source_resets[1];
    assign initialized = !core_reset && initialized_sync[1];
    assign protocol_error = adapter_error || arb_error_sync[1];
    always @(posedge clk_core or posedge reset_async)
        if (reset_async) begin initialized_sync <= 0; arb_error_sync <= 0; end
        else if (core_reset) begin initialized_sync <= 0; arb_error_sync <= 0; end
        else begin
            initialized_sync <= {initialized_sync[0], mem_initialized};
            arb_error_sync <= {arb_error_sync[0], arb_error};
        end
    psx_pgate_mem adapter (
        .clk(clk_core), .rst(core_reset),
        .wram_read(wram_read), .wram_write(wram_write), .bios_read(bios_read), .a(a), .bytes(bytes), .dout(dout),
        .wram_ack(wram_ack), .bios_ack(bios_ack), .wram_error(wram_error), .bios_error(bios_error), .din(din),
        .vram_read(vram_read), .vram_write(vram_write), .vram_flush(vram_flush), .vram_addr(vram_addr),
        .vram_halfwords(vram_halfwords), .vram_dout(vram_dout), .vram_read_ack(vram_read_ack), .vram_write_ack(vram_write_ack),
        .vram_read_error(vram_read_error), .vram_write_error(vram_write_error), .vram_din(vram_din), .protocol_error(adapter_error),
        .p_valid(s_valid), .p_ready(s_ready), .p_write(s_write), .p_addr(s_addr), .p_halfwords(s_halfwords),
        .p_wdata(s_wdata), .p_byte_en(s_byte_en), .p_rsp_valid(s_rsp_valid), .p_rsp_ready(s_rsp_ready),
        .p_rsp_rdata(s_rsp_rdata), .p_rsp_error(s_rsp_error)
    );
    for (genvar port = 0; port < 2; port = port + 1) begin : crossing
        psx_mem_cdc cdc (
            .src_clk(clk_core), .dst_clk(mem_clk), .reset_async(reset_async || mem_reset),
            .src_reset(source_resets[port]), .dst_reset(),
            .s_valid(s_valid[port]), .s_ready(s_ready[port]), .s_write(s_write[port]), .s_addr(s_addr[port]),
            .s_halfwords(s_halfwords[port]), .s_wdata(s_wdata[port]), .s_byte_en(s_byte_en[port]),
            .s_rsp_valid(s_rsp_valid[port]), .s_rsp_ready(s_rsp_ready[port]), .s_rsp_rdata(s_rsp_rdata[port]), .s_rsp_error(s_rsp_error[port]),
            .d_valid(p_valid[port]), .d_ready(p_ready[port]), .d_write(p_write[port]), .d_addr(p_addr[port]),
            .d_halfwords(p_halfwords[port]), .d_wdata(p_wdata[port]), .d_byte_en(p_byte_en[port]),
            .d_rsp_valid(p_rsp_valid[port]), .d_rsp_ready(p_rsp_ready[port]), .d_rsp_rdata(p_rsp_rdata[port]), .d_rsp_error(p_rsp_error[port])
        );
    end
    psx_mem_pocket #(.PORTS(2), .T_INIT(T_INIT), .T_REFI(T_REFI)) memory (
        .clk_74a(clk_74a), .reset_async(reset_async), .client_clk(mem_clk), .client_reset(mem_reset),
        .initialized(mem_initialized), .protocol_error(arb_error),
        .p_valid(p_valid), .p_write(p_write), .p_ready(p_ready), .p_addr(p_addr), .p_halfwords(p_halfwords),
        .p_wdata(p_wdata), .p_byte_en(p_byte_en), .p_rsp_valid(p_rsp_valid), .p_rsp_ready(p_rsp_ready),
        .p_rsp_rdata(p_rsp_rdata), .p_rsp_error(p_rsp_error),
        .dram_a(dram_a), .dram_ba(dram_ba), .dram_dqm(dram_dqm), .dram_ras_n(dram_ras_n),
        .dram_cas_n(dram_cas_n), .dram_we_n(dram_we_n), .dram_cke(dram_cke), .dram_dq(dram_dq), .dram_clk(dram_clk)
    );
endmodule
`default_nettype wire
