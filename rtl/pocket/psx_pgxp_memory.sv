// SPDX-License-Identifier: MIT
// Ordered precision service beside the existing main-RAM controller.
// The native controller supplies events in physical transaction order at
// clk3x; the shadow/filter/client responses run on clk1x. No guest payload
// passes through this module. Stable Native-Off starts no shadow accesses;
// an already accepted access drains before its physical client is released.
`default_nettype none
module psx_pgxp_memory (
    input wire clk1x, clk3x, init, reset, invalidate, enabled,
    input wire event_valid,
    input wire [79:0] event_data,
    output wire queue_ready, queue_almost_full,
    output wire cpu_valid,
    input wire cpu_ready,
    output wire [7:0] cpu_tag,
    output wire [63:0] cpu_record,
    output wire gpu_valid,
    input wire gpu_ready,
    output wire [63:0] gpu_record,
    output wire idle, initializing,
    output wire ch4_req,ch4_rnw,
    output wire [26:0] ch4_address,
    output wire [31:0] ch4_data,
    output wire [3:0] ch4_mask,
    input wire ch4_done,
    input wire [31:0] ch4_q
);
    reg [1:0] wr_reset=3,rd_reset=3;
    always @(posedge clk3x)
        if(init || reset || invalidate || !enabled) wr_reset<=3;
        else wr_reset<={wr_reset[0],1'b0};
    always @(posedge clk1x)
        if(init || reset || invalidate || !enabled) rd_reset<=3;
        else rd_reset<={rd_reset[0],1'b0};
    (* async_reg="true" *) reg [1:0] reader_live_wr=0;
    always @(posedge clk3x)
        if(wr_reset[1])reader_live_wr<=0;
        else reader_live_wr<={reader_live_wr[0],!rd_reset[1]};
    wire full,empty,almost_full,pop,backend_ready;
    wire [79:0] head;
    reg head_valid=0;
    assign queue_ready=enabled && !wr_reset[1] && reader_live_wr[1];
    assign queue_almost_full=!queue_ready || full || almost_full;
    assign pop=!rd_reset[1] && !empty && (!head_valid || backend_ready);
    always @(posedge clk1x)
        if(rd_reset[1])head_valid<=0;
        else if(pop)head_valid<=1;
        else if(head_valid && backend_ready)head_valid<=0;
    psx_pgxp_event_fifo events (
        .wr_rst_i(wr_reset[1]),.wr_clk_i(clk3x),
        .wr_en_i(event_valid && queue_ready),.wr_data_i(event_data),
        .rd_rst_i(rd_reset[1]),.rd_clk_i(clk1x),.rd_en_i(pop),.rd_data_o(head),
        .full_o(full),.empty_o(empty),.almost_full_o(almost_full)
    );
    wire mv,mr,mw,mdone,backend_idle;
    wire [24:0] ma;
    wire [63:0] md,mq;
    psx_pgxp_event_backend backend (
        .clk(clk1x),.reset(reset || init),.invalidate(invalidate),.enabled(enabled),
        .event_valid(head_valid),.event_ready(backend_ready),.event_data(head),
        .cpu_valid(cpu_valid),.cpu_ready(cpu_ready),.cpu_tag(cpu_tag),.cpu_record(cpu_record),
        .gpu_valid(gpu_valid),.gpu_ready(gpu_ready),.gpu_record(gpu_record),
        .idle(backend_idle),.initializing(initializing),
        .mem_valid(mv),.mem_ready(mr),.mem_write(mw),.mem_address(ma),.mem_data(md),
        .mem_done(mdone),.mem_q(mq)
    );
    psx_pgxp_sdram64 transfer (
        .clk(clk1x),.init(init),.valid(mv),.ready(mr),.write_access(mw),.address(ma),.data(md),
        .done(mdone),.q(mq),.ch_req(ch4_req),.ch_rnw(ch4_rnw),.ch_address(ch4_address),
        .ch_data(ch4_data),.ch_mask(ch4_mask),.ch_done(ch4_done),.ch_q(ch4_q)
    );
    assign idle=backend_idle && empty && !head_valid && mr && !mv;
endmodule
`default_nettype wire
