// SPDX-License-Identifier: MIT
// Join an ordered shadow snapshot with the SAME completed CPU data read.
// The snapshot must be queued at the SDRAM transaction's ordering point,
// before any later guest write. A value guard alone cannot repair ordering.
// Native-Off is a combinational bypass with no extra completion cycle.
`default_nettype none
module psx_pgxp_cpu_return (
    input wire clk, reset, enabled, ce,
    input wire request, read_not_write, is_data,
    input wire [31:0] request_address,
    input wire [1:0] request_size,
    output wire [7:0] request_tag,
    input wire raw_done,
    input wire [31:0] raw_data,
    output wire cpu_done,
    output wire [31:0] cpu_data,
    output wire [9:0] cpu_metadata,
    input wire snapshot_valid,
    input wire [7:0] snapshot_tag,
    input wire [63:0] snapshot_record,
    output wire snapshot_ready,
    output wire idle
);
    // Retain this sequence across a guest reset. A late pre-reset snapshot
    // then cannot match the next accepted request. FPGA configuration starts
    // at zero; there is only one CPU data transaction outstanding.
    reg [7:0] next_tag = 0, tag_q = 0;
    reg pending = 0, have_data = 0, have_record = 0, canceled = 0;
    reg [31:0] data_q = 0;
    reg [63:0] record_q = 0;
    reg [1:0] offset_q = 0, size_q = 0;
    wire trackable = request && read_not_write && is_data &&
                     request_address[28:0] < 29'h800000 && request_size != 0;
    wire complete = have_data && (have_record || canceled || !enabled);
    assign request_tag = enabled && trackable && ce ? next_tag : pending ? tag_q : next_tag;
    assign snapshot_ready = 1'b1; // stale or canceled tokens are drained too
    assign idle = !pending;
    wire bypass = !pending;
    assign cpu_done = bypass ? raw_done : complete && ce;
    assign cpu_data = bypass ? raw_data : data_q;
    wire [31:0] observed = size_q == 1 && offset_q[1] ?
                                {data_q[15:0],16'd0} : data_q;
    wire low_half = size_q != 1 || !offset_q[1];
    wire high_half = size_q != 1 || offset_q[1];
    wire valid_x = record_q[40] && low_half && observed[15:0] == record_q[15:0];
    wire valid_y = record_q[41] && high_half && observed[31:16] == record_q[31:16];
    assign cpu_metadata = bypass || !complete || !enabled || canceled ? 10'd0 :
        {valid_y,valid_x,valid_y ? record_q[39:36] : 4'd0,
                           valid_x ? record_q[35:32] : 4'd0};
    always @(posedge clk) begin
        if(reset) begin
            pending<=0; have_data<=0; have_record<=0; canceled<=0;
        end else begin
            // A mode change may discard precision, never a completed guest
            // read. Drain held data even if its snapshot has been canceled.
            if(pending && !enabled) canceled<=1;
            if(pending && raw_done) begin data_q<=raw_data; have_data<=1; end
            if(pending && snapshot_valid && snapshot_tag==tag_q) begin
                record_q<=snapshot_record; have_record<=1;
            end
            if(pending && complete && ce) begin
                pending<=0; have_data<=0; have_record<=0;
            end
            if(enabled && trackable && ce) begin
                // synthesis translate_off
                assert(!pending || (complete && ce))
                    else $fatal(1,"Overlapping PGXP CPU data reads");
                // synthesis translate_on
                pending<=1; have_data<=0; have_record<=0; canceled<=0;
                tag_q<=next_tag; next_tag<=next_tag+1'b1;
                offset_q<=request_address[1:0]; size_q<=request_size;
            end
        end
    end
endmodule
`default_nettype wire
