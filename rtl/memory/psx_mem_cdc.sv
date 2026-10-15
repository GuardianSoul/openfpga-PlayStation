`timescale 1ns/1ps
`default_nettype none

// One outstanding transaction across unrelated clocks. The bundled request
// and response remain stable while their two-flop toggle synchronizers settle.
// Both domains MUST share reset_async; independent resets are unsupported.
// Physical implementation also needs bundled-data and synchronizer constraints.
module psx_mem_cdc (
    input wire src_clk, dst_clk, reset_async,
    output wire src_reset, dst_reset,
    input wire s_valid, s_write,
    output wire s_ready,
    input wire [24:0] s_addr,
    input wire [3:0] s_halfwords,
    input wire [127:0] s_wdata,
    input wire [15:0] s_byte_en,
    output wire s_rsp_valid,
    input wire s_rsp_ready,
    output reg [127:0] s_rsp_rdata,
    output reg s_rsp_error,
    output wire d_valid, d_write,
    input wire d_ready,
    output reg [24:0] d_addr,
    output reg [3:0] d_halfwords,
    output reg [127:0] d_wdata,
    output reg [15:0] d_byte_en,
    input wire d_rsp_valid,
    output wire d_rsp_ready,
    input wire [127:0] d_rsp_rdata,
    input wire d_rsp_error
);
    localparam [1:0] IDLE = 0, SEND = 1, WAIT_RESPONSE = 2, RESPONSE = 3;
    (* async_reg = "true" *) reg [2:0] src_reset_pipe, dst_reset_pipe;
    (* async_reg = "true" *) reg [1:0] request_sync, response_sync;
    reg [1:0] src_state, dst_state;
    reg request_toggle, response_toggle, request_seen;
    reg [173:0] request_payload;
    reg [128:0] response_payload;
    reg d_write_q;

    always @(posedge src_clk or posedge reset_async)
        if (reset_async) src_reset_pipe <= 3'b111;
        else src_reset_pipe <= {src_reset_pipe[1:0], 1'b0};
    always @(posedge dst_clk or posedge reset_async)
        if (reset_async) dst_reset_pipe <= 3'b111;
        else dst_reset_pipe <= {dst_reset_pipe[1:0], 1'b0};
    assign src_reset = src_reset_pipe[2];
    assign dst_reset = dst_reset_pipe[2];
    assign s_ready = !src_reset && src_state == IDLE;
    assign s_rsp_valid = !src_reset && src_state == RESPONSE;
    assign d_valid = !dst_reset && dst_state == SEND;
    assign d_write = d_write_q;
    assign d_rsp_ready = !dst_reset && dst_state == WAIT_RESPONSE;

    always @(posedge src_clk or posedge reset_async) begin
        if (reset_async) begin
            response_sync <= 0; src_state <= IDLE; request_toggle <= 0;
            request_payload <= 0; s_rsp_rdata <= 0; s_rsp_error <= 0;
        end else if (src_reset) begin
            response_sync <= 0; src_state <= IDLE; request_toggle <= 0;
            request_payload <= 0; s_rsp_rdata <= 0; s_rsp_error <= 0;
        end else begin
            response_sync <= {response_sync[0], response_toggle};
            case (src_state)
                IDLE: if (s_valid) begin
                    request_payload <= {s_write, s_addr, s_halfwords, s_wdata, s_byte_en};
                    request_toggle <= !request_toggle;
                    src_state <= WAIT_RESPONSE;
                end
                WAIT_RESPONSE: if (response_sync[1] == request_toggle) begin
                    {s_rsp_error, s_rsp_rdata} <= response_payload;
                    src_state <= RESPONSE;
                end
                RESPONSE: if (s_rsp_ready) src_state <= IDLE;
                default: src_state <= IDLE;
            endcase
        end
    end
    always @(posedge dst_clk or posedge reset_async) begin
        if (reset_async) begin
            request_sync <= 0; dst_state <= IDLE; request_seen <= 0; response_toggle <= 0;
            response_payload <= 0; d_write_q <= 0; d_addr <= 0;
            d_halfwords <= 0; d_wdata <= 0; d_byte_en <= 0;
        end else if (dst_reset) begin
            request_sync <= 0; dst_state <= IDLE; request_seen <= 0; response_toggle <= 0;
            response_payload <= 0; d_write_q <= 0; d_addr <= 0;
            d_halfwords <= 0; d_wdata <= 0; d_byte_en <= 0;
        end else begin
            request_sync <= {request_sync[0], request_toggle};
            case (dst_state)
                IDLE: if (request_sync[1] != request_seen) begin
                    {d_write_q, d_addr, d_halfwords, d_wdata, d_byte_en} <= request_payload;
                    request_seen <= request_sync[1];
                    dst_state <= SEND;
                end
                SEND: if (d_ready) dst_state <= WAIT_RESPONSE;
                WAIT_RESPONSE: if (d_rsp_valid) begin
                    response_payload <= {d_rsp_error, d_rsp_rdata};
                    response_toggle <= request_seen;
                    dst_state <= IDLE;
                end
                default: dst_state <= IDLE;
            endcase
        end
    end
endmodule
`default_nettype wire
