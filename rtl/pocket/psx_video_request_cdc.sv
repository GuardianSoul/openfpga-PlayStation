// SPDX-License-Identifier: MIT
// Ordered video->2x snapshots. The destination must drain faster than source.
// Reuse the same qualified FIFO wrapper as forward settings, at WIDTH=39.
`default_nettype none
module psx_video_request_cdc (
    input wire src_clk,dst_clk,reset_async,
    input wire [38:0] src_data,
    output wire src_ready,src_fault,
    output wire [38:0] dst_data,
    output wire dst_initialized,
    output wire dst_fault
);
    psx_video_config_cdc #(.WIDTH(39),.ADDR_WIDTH(4)) channel (
        .src_clk(src_clk),.dst_clk(dst_clk),.reset_async(reset_async),
        .src_valid(1'b1),.src_data(src_data),.src_ready(src_ready),
        .src_fault(src_fault),.dst_data(dst_data),.dst_update(),
        .dst_initialized(dst_initialized));
    (* async_reg="true", preserve, dont_merge *) reg fault_meta=0,fault_sync=0;
    always @(posedge dst_clk) begin
        if (!dst_initialized) begin fault_meta<=0;fault_sync<=0;end
        else begin fault_meta<=src_fault;fault_sync<=fault_meta;end
    end
    assign dst_fault=fault_sync;
endmodule
`default_nettype wire
