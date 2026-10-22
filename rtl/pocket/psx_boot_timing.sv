// SPDX-License-Identifier: MIT
// Launch timestamps for a diagnostic build (docs/pocket-launch-timing.md):
// where the time goes between picking a game and its first picture.
//
// One free-running clk_74a tick counter from reset (power_reset_n: the
// bitstream is configured), and the counter value at the first rise of each
// event input (0: not seen). Read out in the boot-log slot: word 0 "TIME",
// word 1 the counter now, words 2..31 events 0..29 (EVENTS at most 30).
// Events are host-clock levels (or flags the caller has synchronised).
// The stamps live in one block RAM, one write a clock: events that rise
// together are stamped up to EVENTS ticks (0.2 us) late (one tick when alone). Read path: one
// register, like psx_perf_log's, so the saved file keeps its one-read-late
// order.
`default_nettype none
module psx_boot_timing #(
    parameter integer EVENTS = 30
) (
    input wire clk,
    input wire reset,
    input wire [EVENTS-1:0] event_in,
    input wire [4:0] read_index,
    output reg [31:0] read_data
);
    reg [31:0] now = 0;
    reg [EVENTS-1:0] seen = 0, pending = 0;
    (* ramstyle = "M10K" *) reg [31:0] stamp [0:31];
    integer i;
    initial for (i = 0; i < 32; i = i + 1) stamp[i] = 0;
    // The lowest pending event is stamped this clock.
    reg [4:0] pick;
    reg any;
    always @* begin
        any = 1'b0; pick = 5'd0;
        for (i = EVENTS - 1; i >= 0; i = i - 1)
            if (pending[i]) begin any = 1'b1; pick = i[4:0]; end
    end
    always @(posedge clk) begin
        if (reset) begin
            now <= 0; seen <= 0; pending <= 0;
        end else begin
            now <= now + 1'b1;
            for (i = 0; i < EVENTS; i = i + 1)
                if (event_in[i] && !seen[i]) begin seen[i] <= 1'b1; pending[i] <= 1'b1; end
            if (any) pending[pick] <= 1'b0;
        end
    end
    always @(posedge clk) if (!reset && any) stamp[pick + 5'd2] <= now;
    reg [31:0] q, now_q;
    reg [1:0] sel;
    always @(posedge clk) begin
        q <= stamp[read_index];
        sel <= read_index == 5'd0 ? 2'd0 : read_index == 5'd1 ? 2'd1 : 2'd2;
        now_q <= now;
    end
    always @* case (sel)
        2'd0: read_data = 32'h54494d45;   // "TIME"
        2'd1: read_data = now_q;
        default: read_data = q;
    endcase
endmodule
`default_nettype wire
