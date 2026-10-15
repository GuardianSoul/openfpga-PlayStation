// SPDX-License-Identifier: MIT
// Ordered video report events for the CPU domain. Dot ticks are strobes,
// not sampled clock levels. The consumer must count every asserted strobe.
// Uses the pinned BSD dual_clock_fifo; no payload crosses outside its RAM.
//
// Qualified source: dot spacing >=4 video edges; non-dot report changes
// spacing >=3 video edges (coincident changes share a packet). Destination
// must run continuously at the Pocket 1x clock. Bursts/real scanout need
// integration checks; exceeding the queue capacity latches an explicit fault.
// Startup events before both reset releases are intentionally not accepted.
`default_nettype none
module psx_video_reports_cdc (
    input wire src_clk,dst_clk,reset_async,
    input wire [8:0] src_levels,
    input wire src_dot,
    output wire src_ready,
    output reg src_fault=0,
    output wire [8:0] dst_levels,
    output wire dst_dot,
    output wire dst_update,
    output reg dst_initialized=0,
    output wire dst_fault
);
    (* async_reg="true", preserve, dont_merge *) reg [2:0] src_reset_pipe=7,dst_reset_pipe=7;
    always @(posedge src_clk or posedge reset_async)
        if(reset_async)src_reset_pipe<=7;else src_reset_pipe<={src_reset_pipe[1:0],1'b0};
    always @(posedge dst_clk or posedge reset_async)
        if(reset_async)dst_reset_pipe<=7;else dst_reset_pipe<={dst_reset_pipe[1:0],1'b0};
    wire src_reset=src_reset_pipe[2],dst_reset=dst_reset_pipe[2];
    (* async_reg="true", preserve, dont_merge *) reg destination_online_meta=0,destination_online_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg source_online_meta=0,source_online_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg fault_meta=0,fault_sync=0;
    reg [8:0] previous_levels=0;
    reg published=0,read_valid=0;
    wire full,empty;
    wire event_pending=src_dot || !published || src_levels!=previous_levels;
    assign src_ready=!src_reset && destination_online_sync && !full && !src_fault;
    wire push=src_ready && event_pending;
    wire pop=!dst_reset && source_online_sync && !empty;
    wire [9:0] read_data;
    always @(posedge src_clk or posedge src_reset) begin
        if(src_reset)begin
            destination_online_meta<=0;destination_online_sync<=0;
            previous_levels<=0;published<=0;src_fault<=0;
        end else begin
            destination_online_meta<=!dst_reset;destination_online_sync<=destination_online_meta;
            if(destination_online_sync && event_pending && full)src_fault<=1;
            if(push)begin previous_levels<=src_levels;published<=1;end
        end
    end
    always @(posedge dst_clk or posedge dst_reset) begin
        if(dst_reset)begin
            source_online_meta<=0;source_online_sync<=0;
            fault_meta<=0;fault_sync<=0;read_valid<=0;dst_initialized<=0;
        end else begin
            source_online_meta<=!src_reset;source_online_sync<=source_online_meta;
            fault_meta<=src_fault;fault_sync<=fault_meta;
            read_valid<=pop;
            if(pop)dst_initialized<=1;
        end
    end
    dual_clock_fifo #(.ADDR_WIDTH(4),.DATA_WIDTH(10)) snapshots (
        .wr_clk_i(src_clk),.wr_rst_i(src_reset || !destination_online_sync),
        .wr_en_i(push),.wr_data_i({src_dot,src_levels}),.full_o(full),
        .rd_clk_i(dst_clk),.rd_rst_i(dst_reset || !source_online_sync),
        .rd_en_i(pop),.rd_data_o(read_data),.empty_o(empty));
    assign dst_fault=fault_sync;
    assign dst_levels=dst_initialized && !dst_fault ? read_data[8:0] : 9'b0;
    assign dst_update=read_valid && dst_initialized && !dst_fault;
    assign dst_dot=read_valid && dst_initialized && !dst_fault && read_data[9];
endmodule
`default_nettype wire
