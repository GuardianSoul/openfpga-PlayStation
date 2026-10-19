// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Cycle-level model of one AS1C8M16PL CellularRAM in synchronous fixed-
// latency burst mode, for long Verilator runs of rtl/memory/psx_vram_psram.sv
// (scripts/run_gpu_timing.py). It keeps only the data timing the controller
// relies on: edge 0 is the ADV# address edge, read word i is driven after edge
// LC+i, write word i is taken at edge LC+1+i with UB#/LB# byte masks, and CE#
// high ends the burst. Pin timing, refresh, configuration and asynchronous
// accesses are not modelled (sim/pocket/psx_cram_model.sv checks those).
// mem[] index: {die, halfword address[21:0]}. For a full-rate controller
// (psx_vram_psram FULL_RATE) clk is the controller clock and en its clock
// enable: an edge with en high is the chip edge for the pins of the previous
// clk (the phase-shifted chip clock), and read data reaches dq_in after it.
module psx_cram_cycle_model #(parameter integer LC = 3) (
    input  wire        clk,
    input  wire        en,
    input  wire [21:16] a,
    input  wire [15:0] dq_out,
    output reg  [15:0] dq_in = 16'd0,
    input  wire        ce0_n, ce1_n, adv_n, we_n, ub_n, lb_n, cre
);
    reg [15:0] mem [0:(1<<23)-1];
    reg        active = 1'b0, wr = 1'b0;
    reg [22:0] base = 23'd0;
    integer    k = 0;
    longint unsigned write_segments = 0, read_segments = 0, edges = 0, busy_edges = 0;
    always @(posedge clk) begin : chip_edge
        reg [22:0] i;
        if (en) begin
        edges = edges + 1;
        if (!cre && !(ce0_n && ce1_n)) busy_edges = busy_edges + 1;
        if (cre || (ce0_n && ce1_n)) active = 1'b0;
        else begin
            if (!adv_n) begin
                active = 1'b1; wr = !we_n; k = 0;
                if (wr) write_segments = write_segments + 1; else read_segments = read_segments + 1;
                base = {ce0_n, a, dq_out};
            end else if (active) k = k + 1;
            if (active && wr && k >= LC + 1) begin
                i = base + 23'(k - LC - 1);
                if (!lb_n) mem[i][7:0] = dq_out[7:0];
                if (!ub_n) mem[i][15:8] = dq_out[15:8];
            end
            if (active && !wr && k >= LC) dq_in <= mem[base + 23'(k - LC)];
        end
        end
    end
endmodule
