// SPDX-License-Identifier: MIT
// Coherent stable-level configuration snapshot. Not an event/pulse channel.
// The latest source value is eventually delivered; intermediate changes may
// coalesce while a snapshot is outstanding. Payload remains held until ACK.
// Both sides share reset_async. Local reset assertion is asynchronous and
// release takes three local clock edges. No guest clock-enable gates this.
`default_nettype none
module psx_config_cdc #(parameter integer WIDTH=4) (
    input wire src_clk,dst_clk,reset_async,
    input wire [WIDTH-1:0] src_data,
    output wire src_current,
    output reg [WIDTH-1:0] dst_data=0
);
    (* async_reg="true", preserve, dont_merge *) reg [2:0] src_reset_pipe=3'b111;
    (* async_reg="true", preserve, dont_merge *) reg [2:0] dst_reset_pipe=3'b111;
    always @(posedge src_clk or posedge reset_async)
        if(reset_async) src_reset_pipe<=3'b111; else src_reset_pipe<={src_reset_pipe[1:0],1'b0};
    always @(posedge dst_clk or posedge reset_async)
        if(reset_async) dst_reset_pipe<=3'b111; else dst_reset_pipe<={dst_reset_pipe[1:0],1'b0};
    wire src_reset=src_reset_pipe[2],dst_reset=dst_reset_pipe[2];
    (* preserve, dont_merge *) reg [WIDTH-1:0] payload=0;
    (* preserve, dont_merge *) reg request=0,acknowledge=0,published=0;
    (* async_reg="true", preserve, dont_merge *) reg request_meta=0,request_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg acknowledge_meta=0,acknowledge_sync=0;
    assign src_current=!src_reset && published && request==acknowledge_sync && payload==src_data;
    always @(posedge src_clk or posedge src_reset) begin
        if(src_reset) begin
            payload<=0;request<=0;published<=0;
            acknowledge_meta<=0;acknowledge_sync<=0;
        end else begin
            acknowledge_meta<=acknowledge;acknowledge_sync<=acknowledge_meta;
            if(request==acknowledge_sync && (!published || payload!=src_data)) begin
                payload<=src_data;request<=!request;published<=1;
            end
        end
    end
    always @(posedge dst_clk or posedge dst_reset) begin
        if(dst_reset) begin
            request_meta<=0;request_sync<=0;acknowledge<=0;dst_data<=0;
        end else begin
            request_meta<=request;request_sync<=request_meta;
            if(request_sync!=acknowledge) begin
                dst_data<=payload;acknowledge<=request_sync;
            end
        end
    end
endmodule
`default_nettype wire
