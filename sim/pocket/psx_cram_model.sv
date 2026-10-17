// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Simulation-only pin model of one AS1C8M16PL CellularRAM (two dies on CE0#/
// CE1#, A[21:16] + A/DQ[15:0] multiplexed by ADV#) for the psx_vram_psram
// bench. Written from the Alliance datasheet figures used across this repo
// (tACLK 9 ns, tKOH 2 ns, tHZ 7 ns, tSP 3 ns, tHD 2 ns, tCSP 4 ns, tCEM 4 us,
// 150 us power-up, fixed latency: first read data driven after edge LC, first
// write data taken at edge LC+1, edge 0 = the ADV# address edge) and the
// Pocket's measured rules. It observes pins only.
//
// Checked: power-up time; CE#/ADV#/address/WE#/mask/data setup and hold
// around every rising clock edge with CE# low; clock period, pulse widths and
// latency code against the clock (code * tCLK >= T_LAT_MIN); CE# low time
// (tCEM), CE# high time, CE# hold after the last edge; one die at a time;
// asynchronous CRE register writes (ADV# pulse, address setup/hold, WE#
// pulse, tCW/tAW, data setup, stable CRE, clock stopped); bursts only in
// synchronous fixed-latency mode with full refresh; write bursts never cross a
// 128-halfword row; read bursts that cross one return X (the chip would need
// WAIT); read OE# timing and bus contention; no asynchronous accesses (CE#
// low without a clock edge or CRE); and the Pocket rule that no clock edge
// reaches a selected die in asynchronous mode (writes turned into bursts).
// Register writes depend on the die's mode, as on the chip: an asynchronous
// die takes the asynchronous form above (a clock edge then fails); a
// synchronous die takes only the clocked form (CRE, CE#, ADV# and WE# low at
// a rising edge, A[19:18] the register, DQ the value, written when CE#
// rises) and ignores the asynchronous one (ignored_configs counts them).
// START_BCR/START_RCR are the registers at time 0: power-on values, or what
// another core left (synchronous, its own latency).
// Asynchronous single reads/writes (address latched at ADV# rising; tAA,
// tOE, tWP, tCW, tAW, tDW, tRC) are accepted only while the die is in its
// asynchronous power-on mode.
`timescale 1ns/1ps
`default_nettype none
module psx_cram_model #(
    parameter integer CHIP = 0,
    parameter real T_ACLK = 9.0, T_KOH = 2.0, T_HZ = 7.0, T_OLZ = 3.0, T_OE = 20.0,
    parameter real T_SU = 3.0, T_HD = 2.0, T_CSP = 4.0, T_KP = 4.0, T_CLK_MIN = 15.0,
    parameter real T_LAT_MIN = 60.0, T_CEM = 4000.0, T_CPH = 5.0, T_POWER = 150000.0,
    parameter real T_VP = 5.0, T_CVS = 7.0, T_AVS = 5.0, T_AVH = 2.0,
    parameter real T_WP = 45.0, T_CW = 70.0, T_AW = 70.0, T_DW = 20.0, T_AA = 70.0, T_RC = 70.0,
    parameter [15:0] START_BCR = 16'h9D1F, START_RCR = 16'h0010
)(
    input  wire        clk, host_oe,
    input  wire [21:16] a,
    inout  wire [15:0] dq,
    input  wire        ce0_n, ce1_n, adv_n, oe_n, we_n, ub_n, lb_n, cre
);
    `include "psx_vram_psram_windows.svh"

    reg [15:0] mem [0:2*VP_WORDS-1];
    initial for (integer i = 0; i < 2*VP_WORDS; i = i + 1) mem[i] = 16'hxxxx;
    task automatic seed_all;
        // contents for every window word (indexes match vp_index)
        reg [21:0] w;
        for (w = 22'h000000; w < 22'h024000; w = w + 1) seed_one(w);
        for (w = 22'h040000; w < 22'h044000; w = w + 1) seed_one(w);
        for (w = 22'h060000; w < 22'h070000; w = w + 1) seed_one(w);
        for (w = 22'h080000; w < 22'h0A0000; w = w + 1) seed_one(w);
        for (w = 22'h0E0000; w < 22'h100000; w = w + 1) seed_one(w);
        for (w = 22'h200000; w < 22'h204000; w = w + 1) seed_one(w);
        for (w = 22'h3FC000; w != 22'h000000; w = w + 1) seed_one(w);
    endtask
    task automatic seed_one(input [21:0] w);
        integer i;
        i = vp_index(w);
        mem[2*i] = vp_seed(CHIP, w, 0); mem[2*i+1] = vp_seed(CHIP, w, 1);
    endtask
    initial seed_all();

    function automatic integer slot(input die, input [21:0] ha);
        integer i;
        i = vp_index({die, ha[21:1]});
        if (i < 0) begin $display("CRAM%0d FAIL address outside modelled windows die %0d %h", CHIP, die, ha); $fatal(1); end
        slot = 2*i + ha[0];
    endfunction

    // ---- registers
    reg [15:0] bcr [0:1], rcr [0:1];
    initial begin bcr[0] = START_BCR; bcr[1] = START_BCR; rcr[0] = START_RCR; rcr[1] = START_RCR; end

    // ---- bookkeeping
    integer async_reads = 0, async_writes = 0, sync_configs = 0, ignored_configs = 0;
    integer clk_edges = 0, config_writes = 0, commands = 0, reads = 0, writes = 0, row_stalls = 0, errors = 0;
    real    max_ce_low = 0.0;
    wire    ce_n = ce0_n && ce1_n;
    reg     asy = 0, die = 0, cfg = 0, cfgs = 0, burst = 0, wr = 0, edge_seen = 0, latched = 0, committed = 0, stalled = 0;
    reg [21:0] addr = 0, start = 0;
    reg [15:0] cfg_val = 0;
    reg [1:0]  cfg_sel = 0;
    integer cycles = 0, code = 0, beats = 0;
    realtime t_ce_fall = -1e9, t_ce_rise = -1e9, t_rise = -1e9, t_prev_rise = -1e9, t_fall = -1e9;
    realtime t_adv_fall = -1e9, t_adv_rise = -1e9, t_we_fall = -1e9, t_oe_fall = -1e9;
    realtime t_a = -1e9, t_dq = -1e9, t_adv = -1e9, t_we = -1e9, t_bm = -1e9, t_cre = -1e9;

    task automatic fail(input [8*64-1:0] what);
        begin $display("CRAM%0d FAIL %0s at %0t", CHIP, what, $realtime); errors = errors + 1; $fatal(1); end
    endtask

    // ---- output driver
    reg        drive = 0;
    reg [15:0] drive_data = 16'hxxxx;
    assign dq = drive ? drive_data : 16'bz;
    always @* if (host_oe === 1'b1 && drive) fail("bus contention");

    // ---- input change tracking and hold checks (inputs sampled at a rising edge)
    wire sync_sel = !ce_n && (!cfg || cfgs) && !asy;      // inputs sampled at clock edges
    wire latch_sel = !ce_n && ((cfg && !cfgs) || asy);    // asynchronous forms
    always @(a) begin
        if (sync_sel && $realtime - t_rise < T_HD) fail("address hold after clock");
        if (!ce_n && cfg && !cfgs && latched && !committed) fail("config address changed");
        if (latch_sel && $realtime - t_adv_rise < T_AVH) fail("latched address hold");
        if (!ce_n && asy && latched) fail("async address changed");
        t_a = $realtime;
    end
    always @(dq) if (host_oe === 1'b1) begin
        if (sync_sel && $realtime - t_rise < T_HD) fail("data hold after clock");
        if (latch_sel && $realtime - t_adv_rise < T_AVH) fail("latched address/data hold");
        t_dq = $realtime;
    end
    always @(adv_n) begin
        if (sync_sel && $realtime - t_rise < T_HD) fail("ADV# hold after clock");
        t_adv = $realtime;
        if (!adv_n) t_adv_fall = $realtime;
    end
    always @(we_n) begin
        if (sync_sel && $realtime - t_rise < T_HD) fail("WE# hold after clock");
        t_we = $realtime;
        if (!we_n) t_we_fall = $realtime;
    end
    always @(ub_n or lb_n) begin
        if (sync_sel && $realtime - t_rise < T_HD) fail("UB#/LB# hold after clock");
        t_bm = $realtime;
    end
    always @(cre) begin
        if (!ce_n) fail("CRE changed with CE# low");
        t_cre = $realtime;
    end

    // ---- chip enable
    always @(negedge ce0_n or negedge ce1_n) if (!ce0_n || !ce1_n) begin
        if (!ce0_n && !ce1_n) fail("both dies selected");
        if ($realtime < T_POWER) fail("access before power-up time");
        if ($realtime - t_ce_rise < T_CPH) fail("CE# high time");
        if ($realtime - t_cre < T_SU) fail("CRE setup to CE#");
        t_ce_fall = $realtime; die = ce0_n; cfg = cre; asy = !cre && bcr[ce0_n][15];
        cfgs = cre && !bcr[ce0_n][15];   // a synchronous die: the clocked register write
        burst = 0; edge_seen = 0; latched = 0; committed = 0; stalled = 0; beats = 0;
    end

    task automatic set_register;
        begin
            case (cfg_sel)
            2'b10: bcr[die] = cfg_val;
            2'b00: rcr[die] = cfg_val;
            default: fail("unsupported config register");
            endcase
            config_writes = config_writes + 1; committed = 1;
        end
    endtask

    task automatic commit;
        begin
            if ($realtime - t_we_fall < T_WP) fail("config WE# pulse");
            if ($realtime - t_ce_fall < T_CW) fail("config tCW");
            if ($realtime - t_adv_fall < T_AW) fail("config tAW");
            if ($realtime - t_dq < T_DW) fail("config data setup");
            if (dq !== cfg_val) fail("config data differs from address");
            set_register();
        end
    endtask

    always @(posedge adv_n) if (latch_sel) begin
        t_adv_rise = $realtime;
        if ($realtime - t_adv_fall < T_VP) fail("config ADV# pulse");
        if ($realtime - t_ce_fall < T_CVS) fail("config CE# to ADV#");
        if ($realtime - t_a < T_AVS || $realtime - t_dq < T_AVS) fail("config address setup");
        if (^{a, dq} === 1'bx) fail("config address undriven");
        latched = 1; cfg_val = dq; cfg_sel = a[19:18]; addr = {a, dq};
    end
    always @(posedge we_n) if (!ce_n && cfg && !cfgs && latched && !committed) commit();

    // ---- asynchronous single accesses (only while the die is in asynchronous mode)
    always @(posedge we_n) if (!ce_n && asy) begin : async_write
        integer s;
        if (!latched) fail("async write without an address");
        if ($realtime - t_we_fall < T_WP) fail("async WE# pulse");
        if ($realtime - t_ce_fall < T_CW) fail("async tCW");
        if ($realtime - t_adv_fall < T_AW) fail("async tAW");
        if ($realtime - t_dq < T_DW || $realtime - t_bm < T_DW) fail("async data/mask setup");
        if (host_oe !== 1'b1) fail("async write data not driven");
        s = slot(die, addr);
        if (!lb_n) begin if (^dq[7:0] === 1'bx) fail("write byte undriven"); mem[s][7:0] = dq[7:0]; end
        if (!ub_n) begin if (^dq[15:8] === 1'bx) fail("write byte undriven"); mem[s][15:8] = dq[15:8]; end
        async_writes = async_writes + 1; committed = 1;
    end
    always @(negedge we_n) if (!ce_n && asy && !oe_n) fail("async WE# low with OE# low");

    always @(posedge ce0_n or posedge ce1_n) if (ce0_n && ce1_n && t_ce_fall > 0) begin
        if ($realtime - t_ce_fall > max_ce_low) max_ce_low = $realtime - t_ce_fall;
        if ($realtime - t_ce_fall > T_CEM) fail("CE# low longer than tCEM");
        if (cfgs) begin
            if (!edge_seen) ignored_configs = ignored_configs + 1;   // asynchronous form: ignored
            else begin
                if ($realtime - t_rise < T_HD) fail("CE# hold after clock");
                set_register(); sync_configs = sync_configs + 1;
            end
        end else if (cfg) begin
            if (latched && !committed) commit();
            if (!committed) fail("config access without a write");
        end else if (asy) begin
            if (!latched) fail("async access without an address");
            if ($realtime - t_ce_fall < T_RC) fail("async cycle time");
            if (!committed) async_reads = async_reads + 1;
        end else begin
            if (!edge_seen) fail("CE# low without a clock edge (asynchronous access)");
            if ($realtime - t_rise < T_HD) fail("CE# hold after clock");
            if (beats == 0) fail("burst terminated before first data");
        end
        t_ce_rise = $realtime; burst = 0; cfg = 0; asy = 0;
        drive_data <= 16'hxxxx; drive <= #(T_HZ) 1'b0;
    end

    always @(negedge oe_n) begin : oe_low
        realtime tv;
        t_oe_fall = $realtime;
        if (!ce_n && burst && !wr) begin drive <= #(T_OLZ) 1'b1; drive_data <= #(T_OLZ) 16'hxxxx; end
        if (!ce_n && asy) begin
            if (!latched || !we_n) fail("async read without an address or with WE# low");
            if (lb_n || ub_n) fail("async read with a byte masked");
            tv = t_adv_fall + T_AA;
            if (t_ce_fall + T_AA > tv) tv = t_ce_fall + T_AA;
            if (t_oe_fall + T_OE > tv) tv = t_oe_fall + T_OE;
            drive <= #(T_OLZ) 1'b1; drive_data <= #(T_OLZ) 16'hxxxx;
            drive_data <= #(tv - $realtime) mem[slot(die, addr)];
        end
    end
    always @(posedge oe_n) begin drive_data <= 16'hxxxx; drive <= #(T_HZ) 1'b0; end

    // ---- clock
    always @(negedge clk) begin
        if ($realtime - t_rise < T_KP) fail("clock high time");
        t_fall = $realtime;
    end
    always @(posedge clk) begin : edge_b
        integer s;
        reg [15:0] v;
        realtime period;
        period = $realtime - t_rise;
        if ($realtime - t_fall < T_KP) fail("clock low time");
        if ((bcr[0][15] && !ce0_n) || (bcr[1][15] && !ce1_n)) fail("clock running while a die is asynchronous");
        clk_edges = clk_edges + 1;
        if (!ce_n) begin
            if ((cfg && !cfgs) || cre != cfg) fail("clock during a configuration access");
            if (period < T_CLK_MIN) fail("clock faster than allowed");
            if ($realtime - t_a < T_SU || $realtime - t_adv < T_SU || $realtime - t_we < T_SU ||
                $realtime - t_bm < T_SU || (host_oe && $realtime - t_dq < T_SU))
                fail("input setup to clock");
            if (cfgs) begin
                // clocked register write: the value at the ADV# edge
                if (!edge_seen) begin
                    if (adv_n || we_n) fail("sync config without ADV#/WE# at its first edge");
                    if ($realtime - t_ce_fall < T_CSP) fail("CE# setup to clock");
                    if (^{a[19:18], dq} === 1'bx) fail("sync config value undriven");
                    cfg_sel = a[19:18]; cfg_val = dq;
                end else if (!adv_n) fail("ADV# low twice in a config write");
                edge_seen = 1;
            end else begin
            edge_seen = 1;
            if (!burst) begin
                if (adv_n) fail("burst without ADV# at its first edge");
                if ($realtime - t_ce_fall < T_CSP) fail("CE# setup to clock");
                if (bcr[die][15] || !bcr[die][14]) fail("burst while not in synchronous fixed-latency mode");
                if (rcr[die] !== 16'h0010) fail("partial refresh configured");
                code = bcr[die][13:11];
                if (code * period < T_LAT_MIN) fail("latency code too small for the clock");
                if (^{a, dq} === 1'bx) fail("burst address undriven");
                start = {a, dq}; addr = {a, dq}; wr = !we_n; burst = 1; cycles = 0;
                commands = commands + 1;
            end else begin
                if (!adv_n) fail("ADV# low inside a burst");
                cycles = cycles + 1;
                if (wr && cycles >= code + 1) begin
                    if (addr[21:7] != start[21:7]) fail("write burst crossed a row");
                    if (host_oe !== 1'b1) fail("write data not driven");
                    s = slot(die, addr);
                    if (!lb_n) begin if (^dq[7:0] === 1'bx) fail("write byte undriven"); mem[s][7:0] = dq[7:0]; end
                    if (!ub_n) begin if (^dq[15:8] === 1'bx) fail("write byte undriven"); mem[s][15:8] = dq[15:8]; end
                    writes = writes + 1; beats = beats + 1; addr = addr + 1;
                end else if (!wr && cycles >= code) begin
                    if (ub_n || lb_n) fail("read with a byte masked");
                    if ($realtime + T_ACLK - t_oe_fall < T_OE) fail("read data before tOE");
                    if (addr[21:7] != start[21:7]) begin stalled = 1; row_stalls = row_stalls + 1; end
                    if (stalled) v = 16'hxxxx;
                    else begin s = slot(die, addr); v = mem[s]; reads = reads + 1; end
                    drive_data <= #(T_KOH) 16'hxxxx;
                    drive_data <= #(T_ACLK) v;
                    beats = beats + 1; addr = addr + 1;
                end
            end
            end
        end
        t_prev_rise = t_rise; t_rise = $realtime;
    end
endmodule
`default_nettype wire
