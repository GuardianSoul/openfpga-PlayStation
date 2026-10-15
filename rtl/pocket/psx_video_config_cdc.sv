// SPDX-License-Identifier: MIT
// Ordered CPU settings snapshots. The production experiment supplies one
// snapshot per 1x clock; supported destination clocks must drain faster.
// No coalescing. A full queue is a latched fault, never a silent overwrite.
// Payload is owned by the pinned BSD dual_clock_fifo; notices stay upstream.
`default_nettype none
module psx_video_config_cdc #(
    parameter integer WIDTH=84,
    parameter integer ADDR_WIDTH=4
) (
    input wire src_clk,dst_clk,reset_async,
    input wire src_valid,
    input wire [WIDTH-1:0] src_data,
    output wire src_ready,
    output wire [WIDTH-1:0] dst_data,
    output reg dst_update=0,dst_initialized=0,
    output reg src_fault=0
);
    (* async_reg="true", preserve, dont_merge *) reg [2:0] src_reset_pipe=3'b111;
    (* async_reg="true", preserve, dont_merge *) reg [2:0] dst_reset_pipe=3'b111;
    always @(posedge src_clk or posedge reset_async)
        if(reset_async) src_reset_pipe<=7; else src_reset_pipe<={src_reset_pipe[1:0],1'b0};
    always @(posedge dst_clk or posedge reset_async)
        if(reset_async) dst_reset_pipe<=7; else dst_reset_pipe<={dst_reset_pipe[1:0],1'b0};
    wire src_reset=src_reset_pipe[2],dst_reset=dst_reset_pipe[2];
    (* async_reg="true", preserve, dont_merge *) reg source_online_meta=0,source_online_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg destination_online_meta=0,destination_online_sync=0;
    always @(posedge src_clk or posedge src_reset) begin
        if(src_reset) begin destination_online_meta<=0;destination_online_sync<=0;src_fault<=0;end
        else begin
            destination_online_meta<=!dst_reset;destination_online_sync<=destination_online_meta;
            if(src_valid && destination_online_sync && full) src_fault<=1;
        end
    end
    always @(posedge dst_clk or posedge dst_reset) begin
        if(dst_reset) begin source_online_meta<=0;source_online_sync<=0;dst_update<=0;dst_initialized<=0;end
        else begin
            source_online_meta<=!src_reset;source_online_sync<=source_online_meta;
            dst_update<=pop;
            if(pop) dst_initialized<=1;
        end
    end
    wire full,empty;
    assign src_ready=!src_reset && destination_online_sync && !full && !src_fault;
    wire push=src_valid && src_ready;
    wire pop=!dst_reset && source_online_sync && !empty;
    dual_clock_fifo #(.ADDR_WIDTH(ADDR_WIDTH),.DATA_WIDTH(WIDTH)) snapshots (
        .wr_clk_i(src_clk),.wr_rst_i(src_reset || !destination_online_sync),.wr_en_i(push),.wr_data_i(src_data),.full_o(full),
        .rd_clk_i(dst_clk),.rd_rst_i(dst_reset || !source_online_sync),.rd_en_i(pop),.rd_data_o(dst_data),.empty_o(empty));
    // dst_data is undefined until dst_initialized; no extra fabric output
    // bank is needed. The video consumer must remain reset until then.
endmodule
`default_nettype wire
