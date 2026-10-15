// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Functional single-chip SDR SDRAM model for the Pocket controller tests:
// CAS latency CAS (2 or 3, checked against the mode register), burst 2
// reads, single-location writes, clocked by the controller's chip clock.
// Read timing follows the AS4C32M16MSA datasheet (p9 diagram, p16 table):
// for a READ at chip edge T0, beat k is launched by edge T(CAS-1+k), valid
// from that edge + T_AC until the next edge + T_OH, and X in between (so a
// capture outside the window fails the golden compare). T_RET adds the
// board/input delay back to the FPGA pins. With T_AC = T_OH = T_RET = 0 the
// old ideal model (beats after edges T2, T3 for CL2) was one launch edge late.
// Reports every command-timing violation; a READ or WRITE to a precharging
// or idle bank returns garbage, as the Pocket's chip does.
// Storage covers 4 banks x 64 rows (row bits [5:0]) x 1024 columns.
`timescale 1ns/1ps
`default_nettype none
module sdram_chip_model #(
    parameter integer T_RCD = 2, T_RP = 2, T_RAS = 5, T_RC = 7, T_RFC = 9, T_WR = 2, T_MRD = 2,
    parameter integer CAS = 2,
    parameter real T_AC = 6.0, T_OH = 2.5, T_RET = 0.0,   // ns: clock-to-data max, data hold min, return path
    parameter STRICT_RFC = 1,           // 0: report tRFC misses as warnings only
    parameter NAME = "chip"
) (
    input  wire        clk,             // chip clock
    input  wire        cke, ncs, nras, ncas, nwe,
    input  wire [12:0] a,
    input  wire [1:0]  ba,
    input  wire [1:0]  dqm,
    inout  wire [15:0] dq
);
    reg [15:0] mem [0:(1<<18)-1];
    integer cyc = 0, errors = 0, warnings = 0, refreshes = 0, reads = 0, writes = 0;
    reg        moded = 0;
    reg [3:0]  bank_open = 0;
    reg [12:0] open_row [0:3];
    integer    act_at [0:3], pre_at [0:3], idle_at [0:3];
    integer    ref_at = -100, mrs_at = -100, i;
    reg [15:0] mode = 0;

    // read return pipeline (chip cycles): beat_valid[j] set at edge c is
    // launched by edge c+1+j
    reg [15:0] beat_data [0:5];
    reg [5:0]  beat_valid = 0;
    reg [15:0] bus = 0;
    reg        drive = 0, launched = 0;
    assign dq = drive ? bus : 16'bz;

    function [17:0] index(input [1:0] b, input [12:0] r, input [9:0] c);
        index = {b, r[5:0], c};
    endfunction

    initial begin
        for (i = 0; i < (1<<18); i = i + 1) mem[i] = (i * 16'h9e37) ^ (i >> 5);
        for (i = 0; i < 4; i = i + 1) begin
            open_row[i] = 0; act_at[i] = -100; pre_at[i] = -100; idle_at[i] = -100;
        end
        for (i = 0; i < 6; i = i + 1) beat_data[i] = 0;
    end

    task automatic fail(input [8*64-1:0] what);
        begin
            errors = errors + 1;
            if (errors <= 20) $display("ERROR %0s cycle %0d: %0s", NAME, cyc, what);
        end
    endtask

    wire [2:0] command = {nras, ncas, nwe};
    always @(posedge clk) begin : chip
        integer b, k;
        reg [9:0] col;
        reg [15:0] hword;
        cyc = cyc + 1;
        // Bus: the beat launched by the previous edge goes invalid T_OH after
        // this one; a beat launched by this edge is valid T_AC after it.
        if (launched) bus <= #(T_OH + T_RET) 16'hxxxx;
        if (beat_valid[0]) begin
            drive <= #(T_OH + T_RET) 1'b1;
            bus <= #(T_AC + T_RET) beat_data[0];
        end else if (launched) drive <= #(T_AC + T_RET) 1'b0;
        launched <= beat_valid[0];
        beat_valid <= beat_valid >> 1;
        for (k = 0; k < 5; k = k + 1) beat_data[k] <= beat_data[k+1];
        b = ba;
        if (cke && !ncs && command != 3'b111) begin
            if (cyc - ref_at < T_RFC) begin
                if (STRICT_RFC) fail("command within tRFC");
                else warnings = warnings + 1;
            end
            if (cyc - mrs_at < T_MRD) fail("command within tMRD");
        end
        if (cke && !ncs) case (command)
            3'b011: begin // ACTIVE
                if (!moded) fail("ACTIVE before MODE");
                if (bank_open[b]) fail("ACTIVE to open bank");
                if (cyc < idle_at[b]) fail("ACTIVE before precharge done (tRP/tWR)");
                if (cyc - act_at[b] < T_RC) fail("ACTIVE within tRC");
                bank_open[b] = 1; open_row[b] = a; act_at[b] = cyc;
            end
            3'b101, 3'b100: begin // READ / WRITE
                col = a[9:0];
                if (!bank_open[b]) fail(command == 3'b101 ? "READ to closed or precharging bank" : "WRITE to closed or precharging bank");
                else if (cyc - act_at[b] < T_RCD) fail("READ/WRITE within tRCD");
                if (command == 3'b101) begin
                    reads = reads + 1;
                    if (dqm != 0) fail("DQM set on READ");
                    beat_valid[CAS-2] <= 1; beat_valid[CAS-1] <= 1;
                    beat_data[CAS-2] <= bank_open[b] ? mem[index(b, open_row[b], col)] : 16'hdead;
                    beat_data[CAS-1] <= bank_open[b] ? mem[index(b, open_row[b], col ^ 10'd1)] : 16'hbeef;
                    if (a[10]) begin
                        bank_open[b] = 0;
                        // precharge starts after the burst, not before tRAS
                        pre_at[b] = (cyc + 2 > act_at[b] + T_RAS) ? cyc + 2 : act_at[b] + T_RAS;
                        idle_at[b] = pre_at[b] + T_RP;
                    end
                end else begin
                    writes = writes + 1;
                    if (bank_open[b]) begin
                        hword = mem[index(b, open_row[b], col)];
                        if (!dqm[0]) hword[7:0] = dq[7:0];
                        if (!dqm[1]) hword[15:8] = dq[15:8];
                        mem[index(b, open_row[b], col)] = hword;
                    end
                    if (a[10]) begin
                        bank_open[b] = 0;
                        pre_at[b] = (cyc + T_WR > act_at[b] + T_RAS) ? cyc + T_WR : act_at[b] + T_RAS;
                        idle_at[b] = pre_at[b] + T_RP;
                    end
                end
            end
            3'b010: begin // PRECHARGE
                if (a[10]) begin
                    for (k = 0; k < 4; k = k + 1) if (bank_open[k]) begin
                        if (cyc - act_at[k] < T_RAS) fail("PRECHARGE within tRAS");
                        bank_open[k] = 0; idle_at[k] = cyc + T_RP;
                    end
                end else if (bank_open[b]) begin
                    if (cyc - act_at[b] < T_RAS) fail("PRECHARGE within tRAS");
                    bank_open[b] = 0; idle_at[b] = cyc + T_RP;
                end
            end
            3'b001: begin // AUTO REFRESH
                refreshes = refreshes + 1;
                for (k = 0; k < 4; k = k + 1) begin
                    if (bank_open[k]) fail("REFRESH with an open bank");
                    if (cyc < idle_at[k]) fail("REFRESH before precharge done");
                end
                ref_at = cyc;
            end
            3'b000: begin // LOAD MODE
                if (bank_open != 0) fail("MODE with an open bank");
                if (ba == 0) begin
                    mode = a; moded = 1; mrs_at = cyc;
                    if (a != (13'h0201 | (CAS << 4))) fail("unexpected mode register value");
                end
            end
            3'b111: ;
            default: fail("unsupported command");
        endcase
        // A refresh is due on average every 7.8 us; checked by the bench.
    end
endmodule
`default_nettype wire
