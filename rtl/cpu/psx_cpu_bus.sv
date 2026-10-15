`timescale 1ns/1ps
`default_nettype none

// Fixed retail RAM/BIOS map, scratchpad, and a separate peripheral bus.
// Instruction cache and memory-control timing/configuration are not implemented.
module psx_cpu_bus (
    input wire clk, rst, cache_isolated,
    input wire bus_valid, bus_write, bus_fetch,
    output wire bus_ready,
    input wire [31:0] bus_addr, bus_wdata, bus_store_raw,
    input wire [3:0] bus_byte_en,
    input wire [1:0] bus_size,
    output wire bus_rsp_valid,
    input wire bus_rsp_ready,
    output reg [31:0] bus_rsp_rdata,
    output reg bus_rsp_error,
    output wire mem_valid,
    input wire mem_ready,
    output reg mem_write,
    output reg [24:0] mem_addr,
    output wire [3:0] mem_halfwords,
    output reg [127:0] mem_wdata,
    output reg [15:0] mem_byte_en,
    input wire mem_rsp_valid, mem_rsp_error,
    output wire mem_rsp_ready,
    input wire [127:0] mem_rsp_rdata,
    output wire io_valid,
    input wire io_ready,
    output reg io_write,
    output reg [31:0] io_addr, io_wdata, io_store_raw,
    output reg [3:0] io_byte_en,
    output reg [1:0] io_size,
    input wire io_rsp_valid, io_rsp_error,
    output wire io_rsp_ready,
    input wire [31:0] io_rsp_rdata,
    output reg unsupported_cache_mode
);
    localparam [2:0] IDLE=0, MEM_SEND=1, MEM_WAIT=2, IO_SEND=3, IO_WAIT=4, RESPONSE=5;
    reg [2:0] state;
    reg [31:0] scratch [0:255];
    reg [31:0] cache_control;
    wire physical_segment = bus_addr[31:29]==0 || bus_addr[31:29]==4 || bus_addr[31:29]==5;
    wire [28:0] physical = bus_addr[28:0];
    wire ram = physical < 29'h00800000;
    wire bios = physical >= 29'h1FC00000 && physical < 29'h1FC80000;
    wire scratchpad = physical >= 29'h1F800000 && physical < 29'h1F800400;
    wire mmio = physical >= 29'h1F801000 && physical < 29'h1F803000;
    assign bus_ready = !rst && state==IDLE;
    assign bus_rsp_valid = !rst && state==RESPONSE;
    assign mem_valid = !rst && state==MEM_SEND;
    assign mem_rsp_ready = !rst && state==MEM_WAIT;
    assign mem_halfwords = 2;
    assign io_valid = !rst && state==IO_SEND;
    assign io_rsp_ready = !rst && state==IO_WAIT;
    always @(posedge clk) begin
        if (rst) begin
            state<=IDLE; bus_rsp_rdata<=0; bus_rsp_error<=0; cache_control<=0;
            mem_write<=0; mem_addr<=0; mem_wdata<=0; mem_byte_en<=0;
            io_write<=0; io_addr<=0; io_wdata<=0; io_store_raw<=0; io_byte_en<=0; io_size<=0;
            unsupported_cache_mode<=0;
            // Scratchpad contents are unspecified after reset; no bulk clear.
        end else case (state)
            IDLE: if (bus_valid) begin
                bus_rsp_rdata<=0; bus_rsp_error<=0;
                state<=RESPONSE;
                if (bus_size==3 || (bus_fetch && (bus_write || bus_addr[1:0]!=0))) bus_rsp_error<=1;
                else if (bus_addr==32'hFFFE0130 && !bus_fetch) begin
                    if (bus_write) begin
                        for (integer lane=0;lane<4;lane=lane+1)
                            if (bus_byte_en[lane]) cache_control[lane*8 +:8]<=bus_wdata[lane*8 +:8];
                    end else bus_rsp_rdata<=cache_control;
                end else if (!physical_segment) bus_rsp_error<=1;
                else if (ram || bios) begin
                    if (ram && cache_isolated && bus_addr[31:29]!=5) begin
                        // Until a real cache exists, diagnose isolated cache
                        // accesses rather than corrupting backing main RAM.
                        bus_rsp_error<=1; unsupported_cache_mode<=1;
                    end else if (!(bios && bus_write)) begin
                        mem_addr<=bios ? 25'h800000+{7'b0,physical[18:2],1'b0} : {5'b0,physical[20:2],1'b0};
                        mem_write<=bus_write; mem_wdata<={96'b0,bus_wdata}; mem_byte_en<={12'b0,bus_byte_en};
                        state<=MEM_SEND;
                    end
                end else if (scratchpad && !bus_fetch && bus_addr[31:29]!=5) begin
                    if (bus_write) begin
                        for (integer lane=0;lane<4;lane=lane+1)
                            if (bus_byte_en[lane]) scratch[physical[9:2]][lane*8 +:8]<=bus_wdata[lane*8 +:8];
                    end else bus_rsp_rdata<=scratch[physical[9:2]];
                end else if (mmio && !bus_fetch) begin
                    io_addr<={3'b0,physical}; io_write<=bus_write; io_wdata<=bus_wdata;
                    io_store_raw<=bus_store_raw; io_byte_en<=bus_byte_en; io_size<=bus_size; state<=IO_SEND;
                end else bus_rsp_error<=1;
            end
            MEM_SEND: if (mem_ready) state<=MEM_WAIT;
            MEM_WAIT: if (mem_rsp_valid) begin
                bus_rsp_rdata<=mem_write ? 32'b0 : mem_rsp_rdata[31:0];
                bus_rsp_error<=mem_rsp_error; state<=RESPONSE;
            end
            IO_SEND: if (io_ready) state<=IO_WAIT;
            IO_WAIT: if (io_rsp_valid) begin
                bus_rsp_rdata<=io_write ? 32'b0 : io_rsp_rdata;
                bus_rsp_error<=io_rsp_error; state<=RESPONSE;
            end
            RESPONSE: if (bus_rsp_ready) state<=IDLE;
            default: state<=IDLE;
        endcase
    end
endmodule
`default_nettype wire
