// SPDX-License-Identifier: MIT
// Coherent diagnostic snapshots using two dual-clock RAM words. Only latest
// status is promised: intermediate source values may coalesce. Not an event,
// command, memory-service or guest-timing channel.
// The shared asynchronous reset must be glitch-free and independent of guest
// CE/warm reset. Both local resets release after three local clock edges.
`default_nettype none
module psx_status_cdc #(parameter integer WIDTH=69) (
    input wire src_clk,dst_clk,reset_async,
    input wire [WIDTH-1:0] src_data,
    output reg dst_valid=0,
    output wire [WIDTH-1:0] dst_data
);
    (* async_reg="true", preserve, dont_merge *) reg [2:0] src_reset_pipe=7,dst_reset_pipe=7;
    always @(posedge src_clk or posedge reset_async)
        if(reset_async) src_reset_pipe<=7; else src_reset_pipe<={src_reset_pipe[1:0],1'b0};
    always @(posedge dst_clk or posedge reset_async)
        if(reset_async) dst_reset_pipe<=7; else dst_reset_pipe<={dst_reset_pipe[1:0],1'b0};
    wire src_reset=src_reset_pipe[2],dst_reset=dst_reset_pipe[2];
    (* preserve, dont_merge *) reg request=0,acknowledge=0;
    (* async_reg="true", preserve, dont_merge *) reg request_meta=0,request_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg acknowledge_meta=0,acknowledge_sync=0;
    wire publish=!src_reset && request==acknowledge_sync;
    wire capture=!dst_reset && request_sync!=acknowledge;
    // Same synchronous read pattern as the pinned dual_clock_fifo. Do not
    // reset the RAM or its output: dst_valid masks stale data after reset.
    (* ramstyle="M10K, no_rw_check" *) reg [WIDTH-1:0] snapshots[0:1];
    reg [WIDTH-1:0] ram_data;
    always @(posedge src_clk) if(publish) snapshots[!request]<=src_data;
    always @(posedge dst_clk) if(capture) ram_data<=snapshots[request_sync];
    assign dst_data=dst_valid ? ram_data : {WIDTH{1'b0}};
    always @(posedge src_clk or posedge src_reset) begin
        if(src_reset) begin
            request<=0;acknowledge_meta<=0;acknowledge_sync<=0;
        end else begin
            acknowledge_meta<=acknowledge;acknowledge_sync<=acknowledge_meta;
            if(publish) request<=!request;
        end
    end
    always @(posedge dst_clk or posedge dst_reset) begin
        if(dst_reset) begin
            request_meta<=0;request_sync<=0;acknowledge<=0;dst_valid<=0;
        end else begin
            request_meta<=request;request_sync<=request_meta;
            if(capture) begin acknowledge<=request_sync;dst_valid<=1;end
        end
    end
endmodule
`default_nettype wire
