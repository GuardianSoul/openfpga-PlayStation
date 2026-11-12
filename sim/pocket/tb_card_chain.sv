// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Chain bench for memory card 1 and the save-state buffer (run by
// scripts/run_card_chain.py). The four real modules, connected as in the
// save-state core_top (build/ss/ce/save-states/core_top.sv, "--memcard-slot",
// "Save states" and "--vram-x3" blocks):
//
//   bridge (clk_74a) -> psx_memcard_slot (SAVE_BASE 0x30000000)
//     -> psx_save_exp1 up_* (page copy engine shares the outgoing aux port)
//     -> psx_vram_x3 (clk2x -> clk3x)
//     -> psx_vram_psram (SAVE_AUX 1, FULL_RATE 1, LATENCY 6, CLK_KHZ 101606,
//        SLOW_DIV 3, OUT_REG 1) -> two psx_cram_model chips, two dies each
//
// Resets and clocks as in core_top: x3 and the controller reset on
// !cpu_locked, slot and save device on mc_reset2x[1] (two clk2x registers of
// !cpu_locked), park = core_held = !reset_n. The board (DDIO chip clock,
// transport delays) is tb_psx_vram_psram's X3 profile "x3-slow-seed1".
//
// A golden copy of every modelled PSRAM word ({cram1, cram0} per halfword
// address) starts from the chips' seeded contents and takes every write the
// bench issues. Each case ends with a full compare of both chips against it,
// so "card words on die 0", "die 1 untouched" and "the card changes only by
// the bridge's card writes" are one check with counts per region.
//
// Cases (see the runner's docstring for what is and is not covered):
//   1 card load through the bridge window while parked, readback running
//   2 save-window writes while parked (a wake), parked reads
//   3 running: page copies in the save device against bridge card traffic
//   4 running: sequential bridge reads of the save window, card traffic and
//     page copies
//   6 running: save-window uploads at exactly 88 clk_74a per transaction (and
//     88..96) at the start, middle and end of the buffer, per VRAM traffic
//     level 0..4 (4 = 128-word reads and 128-word write streams back to
//     back), then with card accesses and page copies on; prints one LEVEL
//     line per level with the aux latency (slot request to done, clk2x)
//   7 the same with save-window reads
//   8 save by page copies, bridge read-out, buffer overwritten, bridge
//     upload under worst-case VRAM traffic, load by page copies
// Cases 3 and 4 run GPU-style bursts on the controller's DDR port.
// The slot's sticky overflow output (unconnected in core_top) is checked in
// every case and reported the moment it rises.
`timescale 1ns/1ps
`default_nettype none
module tb_card_chain;
    parameter real PERIOD = 14.766;           // clk2x (2:3 with clk3x exact at 1 ps)
    parameter real PHASE = 4.716, CLK_DLY = 3.9, CTL_DLY = 3.9, DQ_DLY = 3.9, RET_DLY = 3.5;
    parameter real T_ACLK = 5.5, T_KOH = 2.0, T_CLK_MIN = 7.5, T_LAT_MIN = 55.0;
    localparam real CPERIOD = PERIOD * 2 / 3;
    localparam integer CARD_WORDS = 32768;
    localparam integer RD_SAMPLE = 5;
    `include "card_chain_windows.svh"   // psx_vram_psram_windows.svh + the middle of the save buffer

    integer seed, phase_ps, wper, n3, n4, cont, gpu, last_case, card_n, save_n, probe_n, up_n, rb_n, holdoff_n;
    initial begin
        if (!$value$plusargs("SEED=%d", seed)) seed = 1;
        if (!$value$plusargs("PHASE74=%d", phase_ps)) phase_ps = 0;
        if (!$value$plusargs("WPER=%d", wper)) wper = 88;       // clk_74a per bridge transaction (APF's fastest)
        if (!$value$plusargs("N3=%d", n3)) n3 = 40;             // page copies in case 3
        if (!$value$plusargs("N4=%d", n4)) n4 = 8192;           // save-window words read in case 4
        if (!$value$plusargs("CONTINUE=%d", cont)) cont = 0;    // 1: run later cases after a failed one
        if (!$value$plusargs("GPU=%d", gpu)) gpu = 1;           // DDR background traffic in cases 3 and 4
        if (!$value$plusargs("LAST_CASE=%d", last_case)) last_case = 8;
        // smoke runs only: fewer card words loaded and read back, fewer save words per half in case 2
        if (!$value$plusargs("CARD_N=%d", card_n)) card_n = CARD_WORDS;
        if (!$value$plusargs("SAVE_N=%d", save_n)) save_n = 16384;
        if (!$value$plusargs("UP_N=%d", up_n)) up_n = 3328;     // case 6 phase A: save words per region and level
        if (!$value$plusargs("RB_N=%d", rb_n)) rb_n = 2048;     // case 7 phase A
        if (!$value$plusargs("PROBE=%d", probe_n)) probe_n = 1;   // 0: skip the park-transition probe
        // card-holdoff (run_save_card_holdoff.py): after case 1 run case 9 only, NB bursts
        if (!$value$plusargs("HOLDOFF=%d", holdoff_n)) holdoff_n = 0;
    end

    // ---------------- clocks ----------------
    reg clk2x = 0, clk1x = 0, clk3x = 0, clk_74a = 0;
    initial begin #(PERIOD/2); forever begin clk2x = 1; clk1x = !clk1x; #(PERIOD/2); clk2x = 0; #(PERIOD/2); end end
    initial begin #(PERIOD/2); forever begin clk3x = 1; #(PERIOD/3); clk3x = 0; #(PERIOD/3); end end
    initial begin #0.001; #(phase_ps / 1000.0); forever #6.734 clk_74a = !clk_74a; end

    // ---------------- core_top's wiring ----------------
    reg cpu_locked = 0, reset_n = 0;
    reg [31:0] bridge_addr = 0, bridge_wr_data = 0;
    reg bridge_wr = 0, bridge_rd = 0, memcard_load_done = 0;
    reg [12:0] save_exp1_addr = 0; reg save_exp1_write = 0; reg [7:0] save_exp1_dataWrite = 0;
    wire [7:0] save_exp1_dataRead;
    reg [7:0] ddr3_BURSTCNT = 0; reg [24:0] ddr3_ADDR = 0; reg ddr3_RD = 0, ddr3_WE = 0;
    reg [63:0] ddr3_DIN = 0; reg [7:0] ddr3_BE = 0;
    wire ddr3_BUSY, ddr3_DOUT_READY; wire [63:0] ddr3_DOUT;

`ifdef SLOT_HOLDOFF
    wire vram_park;              // card-holdoff: !reset_n through the slot's hold-off
`else
    wire vram_park = !reset_n;   // synchronised inside psx_vram_psram
`endif
    wire [15:0] cram0_dq_out, cram1_dq_out;
    wire cram0_dq_oe, cram1_dq_oe;
    wire memcard_overflow;   // clk_74a, sticky: a card/save bridge word was dropped (checked in every case)
    wire mc_busy, mc_aux_req, mc_aux_we, mc_aux_done, mc_card_write;
    wire [20:0] mc_aux_addr; wire [31:0] mc_aux_din, mc_aux_dout, memcard_rd_data;
    reg [1:0] mc_reset2x = 2'b11;
    always @(posedge clk2x) mc_reset2x <= {mc_reset2x[0], !cpu_locked};
`ifdef SLOT_HOLDOFF
    psx_memcard_slot #(.SAVE_BASE(32'h3000_0000), .HOLD_BITS(`SLOT_HOLDOFF)) memcard_slot (
        .park_req(!reset_n), .park(vram_park),
`else
    psx_memcard_slot #(.SAVE_BASE(32'h3000_0000)) memcard_slot (
`endif.clk_74a(clk_74a),.bridge_addr(bridge_addr),.bridge_wr(bridge_wr),
        .bridge_wr_data(bridge_wr_data),.bridge_rd(bridge_rd),.bridge_rd_data(memcard_rd_data),.overflow(memcard_overflow),
        .clk2x(clk2x),.reset_2x(mc_reset2x[1]),
`ifdef SLOT_OVERFLOW_CLEAR
        .overflow_clear(1'b0),   // as core_top ties it (slot with the overflow_clear input)
`endif
        .busy(mc_busy),
        .aux_req(mc_aux_req),.aux_we(mc_aux_we),.aux_addr(mc_aux_addr),.aux_din(mc_aux_din),.aux_dout(mc_aux_dout),
        .aux_done(mc_aux_done),.card_write(mc_card_write),.load_done(memcard_load_done));
    wire cram0_clk_en, cram1_clk_en;
    wire save_trap, save_mode, save_cd_freeze, ss_aux_req, ss_aux_we, ss_aux_done;
    wire [20:0] ss_aux_addr; wire [31:0] ss_aux_din, ss_aux_dout;
    psx_save_exp1 #(.ROM_HEX(""), .SAVE_BASE(32'h3000_0000), .SAVE_SIZE(32'h0040_0000)) save_exp1 (
        .clk2x(clk2x), .reset_2x(mc_reset2x[1]),
        .bus_addr(save_exp1_addr), .bus_write(save_exp1_write), .bus_dataWrite(save_exp1_dataWrite),
        .bus_dataRead(save_exp1_dataRead), .trap(save_trap), .save_mode(save_mode), .cd_freeze(save_cd_freeze),
        .set_sel(), .set_data(),
        .timer_flags(12'd0), .gpu_ranges(63'd0),
        .clk1x(clk1x), .spu_hold(), .spu_quiet(1'b1), .spu_ss_adr(),
        .spu_ss_wdata(), .spu_ss_rden(), .spu_ss_wren(), .spu_ss_rdata(32'd0),
        .spuram_req(), .spuram_rnw(), .spuram_adr(),
        .spuram_wdata(), .spuram_rdata(16'd0), .spuram_done(1'b0),
        .aux_req(ss_aux_req), .aux_we(ss_aux_we), .aux_addr(ss_aux_addr), .aux_din(ss_aux_din),
        .aux_dout(ss_aux_dout), .aux_done(ss_aux_done),
        .up_req(mc_aux_req), .up_we(mc_aux_we), .up_addr(mc_aux_addr), .up_din(mc_aux_din),
        .up_dout(mc_aux_dout), .up_done(mc_aux_done),
        .clk_74a(clk_74a), .savestate_supported(), .savestate_addr(),
        .savestate_size(), .savestate_maxloadsize(),
        .savestate_start(1'b0), .savestate_start_ack(), .savestate_start_busy(),
        .savestate_start_ok(), .savestate_start_err(),
        .savestate_load(1'b0), .savestate_load_ack(), .savestate_load_busy(),
        .savestate_load_ok(), .savestate_load_err(), .core_held(!reset_n),
`ifdef SAVE_DISC_SET
        .disc(3'd0), .disc_set(), .disc_set_tgl(), .disc_silent(), .disc_settled(1'b1), .disc_wrong(1'b0),
`else
        .disc(2'd0),
`endif
        .cd_state(64'd0));
    wire vx3_ddr_busy;
    wire [7:0] vx3_ddr_burstcnt;
    wire [24:0] vx3_ddr_addr;
    wire [63:0] vx3_ddr_dout;
    wire vx3_ddr_dout_ready;
    wire vx3_ddr_rd;
    wire [63:0] vx3_ddr_din;
    wire [7:0] vx3_ddr_be;
    wire vx3_ddr_we;
    wire vx3_aux_req;
    wire vx3_aux_we;
    wire [20:0] vx3_aux_addr;
    wire [31:0] vx3_aux_din;
    wire vx3_aux_done;
    wire vx3_card_write;
    wire [15:0] cram0_dq_oe_pin, cram1_dq_oe_pin;
    wire [21:16] cram0_a, cram1_a;
    wire cram0_adv_n, cram0_cre, cram0_ce0_n, cram0_ce1_n, cram0_oe_n, cram0_we_n, cram0_ub_n, cram0_lb_n;
    wire cram1_adv_n, cram1_cre, cram1_ce0_n, cram1_ce1_n, cram1_oe_n, cram1_we_n, cram1_ub_n, cram1_lb_n;
    reg  [15:0] cram0_dq = 16'hzzzz, cram1_dq = 16'hzzzz;   // the pins as the controller reads them
    psx_vram_x3 vram_x3 (.clk2x(clk2x),.clk3x(clk3x),.reset(!cpu_locked),.ddr_busy(ddr3_BUSY),.c_busy(vx3_ddr_busy),.ddr_burstcnt(ddr3_BURSTCNT),.c_burstcnt(vx3_ddr_burstcnt),.ddr_addr(ddr3_ADDR),.c_addr(vx3_ddr_addr),.ddr_dout(ddr3_DOUT),.c_dout(vx3_ddr_dout),.ddr_dout_ready(ddr3_DOUT_READY),.c_dout_ready(vx3_ddr_dout_ready),.ddr_rd(ddr3_RD),.c_rd(vx3_ddr_rd),.ddr_din(ddr3_DIN),.c_din(vx3_ddr_din),.ddr_be(ddr3_BE),.c_be(vx3_ddr_be),.ddr_we(ddr3_WE),.c_we(vx3_ddr_we),.aux_req(ss_aux_req),.c_aux_req(vx3_aux_req),.aux_we(ss_aux_we),.c_aux_we(vx3_aux_we),.aux_addr(ss_aux_addr),.c_aux_addr(vx3_aux_addr),.aux_din(ss_aux_din),.c_aux_din(vx3_aux_din),.aux_done(ss_aux_done),.c_aux_done(vx3_aux_done),.card_write(mc_card_write),.c_card_write(vx3_card_write));
    psx_vram_psram #(.SAVE_AUX(1), .FULL_RATE(1'b1), .LATENCY(6), .CLK_KHZ(101606), .SLOW_DIV(3), .OUT_REG(1'b1)) vram (.clk(clk3x),.reset(!cpu_locked),.park(vram_park),
        .ddr_busy(vx3_ddr_busy),.ddr_burstcnt(vx3_ddr_burstcnt),.ddr_addr(vx3_ddr_addr),.ddr_dout(vx3_ddr_dout),
        .ddr_dout_ready(vx3_ddr_dout_ready),.ddr_rd(vx3_ddr_rd),.ddr_din(vx3_ddr_din),.ddr_be(vx3_ddr_be),.ddr_we(vx3_ddr_we),
        .aux_req(vx3_aux_req),.aux_we(vx3_aux_we),.aux_addr(vx3_aux_addr),.aux_din(vx3_aux_din),.aux_dout(ss_aux_dout),
        .aux_done(vx3_aux_done),.card_write(vx3_card_write),
        .cram0_a(cram0_a),.cram0_dq_out(cram0_dq_out),.cram0_dq_oe(cram0_dq_oe),.cram0_dq_oe_pin(cram0_dq_oe_pin),.cram0_dq_in(cram0_dq),
        .cram0_clk(cram0_clk_en),.cram0_adv_n(cram0_adv_n),.cram0_cre(cram0_cre),.cram0_ce0_n(cram0_ce0_n),
        .cram0_ce1_n(cram0_ce1_n),.cram0_oe_n(cram0_oe_n),.cram0_we_n(cram0_we_n),.cram0_ub_n(cram0_ub_n),.cram0_lb_n(cram0_lb_n),
        .cram1_a(cram1_a),.cram1_dq_out(cram1_dq_out),.cram1_dq_oe(cram1_dq_oe),.cram1_dq_oe_pin(cram1_dq_oe_pin),.cram1_dq_in(cram1_dq),
        .cram1_clk(cram1_clk_en),.cram1_adv_n(cram1_adv_n),.cram1_cre(cram1_cre),.cram1_ce0_n(cram1_ce0_n),
        .cram1_ce1_n(cram1_ce1_n),.cram1_oe_n(cram1_oe_n),.cram1_we_n(cram1_we_n),.cram1_ub_n(cram1_ub_n),.cram1_lb_n(cram1_lb_n));

    // ---------------- board and chips (tb_psx_vram_psram's X3 board) ----------------
    reg [5:0] d0_a = 0, d1_a = 0; reg [15:0] d0_dq = 0, d1_dq = 0; reg d0_oe = 0, d1_oe = 0;
    reg d0_clk = 0, d1_clk = 0, d0_adv = 1, d1_adv = 1, d0_cre = 0, d1_cre = 0, d0_ce0 = 1, d1_ce0 = 1;
    reg d0_ce1 = 1, d1_ce1 = 1, d0_oen = 1, d1_oen = 1, d0_wen = 1, d1_wen = 1, d0_ub = 1, d1_ub = 1, d0_lb = 1, d1_lb = 1;
    // core_top's altddio_out: a high half-period PHASE ns after a clk3x edge that had the enable set
    reg ddio0 = 0, ddio1 = 0;
    always @(posedge clk3x) fork
        begin #(PHASE) ddio0 = cram0_clk_en; ddio1 = cram1_clk_en; end
        begin #(PHASE + CPERIOD/2) ddio0 = 0; ddio1 = 0; end
    join_none
    always @(ddio0) d0_clk <= #(CLK_DLY) ddio0;                always @(ddio1) d1_clk <= #(CLK_DLY) ddio1;
    always @(cram0_a) d0_a <= #(CTL_DLY) cram0_a;              always @(cram1_a) d1_a <= #(CTL_DLY) cram1_a;
    always @(cram0_adv_n) d0_adv <= #(CTL_DLY) cram0_adv_n;    always @(cram1_adv_n) d1_adv <= #(CTL_DLY) cram1_adv_n;
    always @(cram0_cre) d0_cre <= #(CTL_DLY) cram0_cre;        always @(cram1_cre) d1_cre <= #(CTL_DLY) cram1_cre;
    always @(cram0_ce0_n) d0_ce0 <= #(CTL_DLY) cram0_ce0_n;    always @(cram1_ce0_n) d1_ce0 <= #(CTL_DLY) cram1_ce0_n;
    always @(cram0_ce1_n) d0_ce1 <= #(CTL_DLY) cram0_ce1_n;    always @(cram1_ce1_n) d1_ce1 <= #(CTL_DLY) cram1_ce1_n;
    always @(cram0_oe_n) d0_oen <= #(CTL_DLY) cram0_oe_n;      always @(cram1_oe_n) d1_oen <= #(CTL_DLY) cram1_oe_n;
    always @(cram0_we_n) d0_wen <= #(CTL_DLY) cram0_we_n;      always @(cram1_we_n) d1_wen <= #(CTL_DLY) cram1_we_n;
    always @(cram0_ub_n) d0_ub <= #(CTL_DLY) cram0_ub_n;       always @(cram1_ub_n) d1_ub <= #(CTL_DLY) cram1_ub_n;
    always @(cram0_lb_n) d0_lb <= #(CTL_DLY) cram0_lb_n;       always @(cram1_lb_n) d1_lb <= #(CTL_DLY) cram1_lb_n;
    always @(cram0_dq_out) d0_dq <= #(DQ_DLY) cram0_dq_out;    always @(cram1_dq_out) d1_dq <= #(DQ_DLY) cram1_dq_out;
    always @(cram0_dq_oe) d0_oe <= #(DQ_DLY) cram0_dq_oe;      always @(cram1_dq_oe) d1_oe <= #(DQ_DLY) cram1_dq_oe;
    wire [15:0] bus0 = d0_oe ? d0_dq : 16'bz;
    wire [15:0] bus1 = d1_oe ? d1_dq : 16'bz;
    always @(bus0) cram0_dq <= #(RET_DLY) bus0;
    always @(bus1) cram1_dq <= #(RET_DLY) bus1;
    psx_cram_model #(.CHIP(0), .T_ACLK(T_ACLK), .T_KOH(T_KOH), .T_CLK_MIN(T_CLK_MIN), .T_LAT_MIN(T_LAT_MIN)) chip0 (
        .clk(d0_clk), .host_oe(d0_oe), .a(d0_a), .dq(bus0),
        .ce0_n(d0_ce0), .ce1_n(d0_ce1), .adv_n(d0_adv), .oe_n(d0_oen), .we_n(d0_wen), .ub_n(d0_ub), .lb_n(d0_lb), .cre(d0_cre));
    psx_cram_model #(.CHIP(1), .T_ACLK(T_ACLK), .T_KOH(T_KOH), .T_CLK_MIN(T_CLK_MIN), .T_LAT_MIN(T_LAT_MIN)) chip1 (
        .clk(d1_clk), .host_oe(d1_oe), .a(d1_a), .dq(bus1),
        .ce0_n(d1_ce0), .ce1_n(d1_ce1), .adv_n(d1_adv), .oe_n(d1_oen), .we_n(d1_wen), .ub_n(d1_ub), .lb_n(d1_lb), .cre(d1_cre));

    // ---------------- errors ----------------
    integer errors = 0, case_errors = 0, cur_case = 0;
    task automatic err(input [8*96-1:0] msg);
        begin
            errors = errors + 1; case_errors = case_errors + 1;
            if (case_errors <= 8) $display("ERROR case %0d t=%0t %0s", cur_case, $realtime, msg);
        end
    endtask
    task automatic give_up(input [8*96-1:0] msg);
        begin
            $display("ERROR case %0d t=%0t %0s", cur_case, $realtime, msg);
            $display("CASE %0d FAIL {\"aborted\":1}", cur_case);
            $display("CARD_CHAIN FAIL"); $finish;
        end
    endtask
    always @(posedge clk3x) #0.1 if (cpu_locked && (cram0_dq_oe_pin !== {16{cram0_dq_oe}} || cram1_dq_oe_pin !== {16{cram1_dq_oe}}))
        err("per-pin DQ enables differ");

    // ---------------- golden PSRAM contents ----------------
    // gold[k] = {cram1, cram0} at the model's storage slot k = 2 * vp_index(DDR word) + beat,
    // i.e. halfword address {DDR word[20:0], beat} of die DDR word[21] on both chips.
    reg [31:0] gold [0:2*VP_WORDS-1];
    function automatic integer card_k(input integer i);   // card word i: die 0, halfword 0x40000 + i
        card_k = 2 * vp_index(22'h020000 + i / 2) + (i % 2);
    endfunction
    function automatic integer save_k(input integer i);   // save word i: die 1, halfword i (-1: not modelled)
        integer v;
        begin v = vp_index(22'h200000 + i / 2); save_k = v < 0 ? -1 : 2 * v + (i % 2); end
    endfunction
    integer bad_card, bad_d0, bad_save, bad_d1;
    task automatic compare_all;
        integer k, idx, shown;
        reg [31:0] got;
        begin
            bad_card = 0; bad_d0 = 0; bad_save = 0; bad_d1 = 0; shown = 0;
            for (k = 0; k < 2 * VP_WORDS; k = k + 1) begin
                got = {chip1.mem[k], chip0.mem[k]};
                if (got !== gold[k]) begin
                    idx = k / 2;
                    if (idx >= 32'h20000 && idx < 32'h24000) bad_card = bad_card + 1;
                    else if (idx < 32'h78000) bad_d0 = bad_d0 + 1;
                    else if (idx < 32'h7C000 || idx >= 32'h80000) bad_save = bad_save + 1;
                    else bad_d1 = bad_d1 + 1;
                    if (shown < 4) begin
                        shown = shown + 1;
                        $display("ERROR case %0d chips differ from golden: slot %0d (dense word %h beat %0d) chips %h golden %h",
                            cur_case, k, idx, k % 2, got, gold[k]);
                    end
                end
            end
            if (bad_card + bad_d0 + bad_save + bad_d1 != 0) begin errors = errors + 1; case_errors = case_errors + 1; end
        end
    endtask

    // ---------------- monitors ----------------
    // aux handshakes at each stage of the chain
    integer n_bridge = 0, n_up_req = 0, n_up_done = 0, n_own_done = 0, n_ss_done = 0, n_c_req = 0, n_c_done = 0;
    integer n_cw2 = 0, n_cw3 = 0, n_orphan = 0, n_both = 0, copies = 0;
    integer up_lat = 0, up_lat_max = 0, lat_max = 0, lat_sum = 0, lat_n = 0;   // lat_*: per traffic level
    reg in_up = 0, in_c = 0;
    always @(posedge clk2x) begin
        if (mc_card_write) n_cw2 = n_cw2 + 1;
        if (mc_aux_req && !in_up && !mc_aux_done) begin in_up <= 1; n_up_req = n_up_req + 1; up_lat = 0; end
        else if (in_up) up_lat = up_lat + 1;
        if (mc_aux_done) begin
            n_up_done = n_up_done + 1; in_up <= 0;
            if (!in_up) err("up_done without a slot request");
            if (up_lat + 1 > up_lat_max) up_lat_max = up_lat + 1;
            if (up_lat + 1 > lat_max) lat_max = up_lat + 1;
            lat_sum = lat_sum + up_lat + 1; lat_n = lat_n + 1;
        end
        if (ss_aux_done) begin
            n_ss_done = n_ss_done + 1;
            if (save_exp1.own_req && save_exp1.up_go) begin n_both = n_both + 1; err("aux_done with a card and a copy access both granted"); end
            if (!save_exp1.own_req && !save_exp1.up_go) begin n_orphan = n_orphan + 1; err("aux_done with no access granted"); end
            if (save_exp1.own_req) n_own_done = n_own_done + 1;
        end
    end
    always @(posedge clk3x) begin
        if (vx3_card_write) n_cw3 = n_cw3 + 1;
        if (vx3_aux_req && !in_c) begin in_c <= 1; n_c_req = n_c_req + 1; end
        if (vx3_aux_done) begin
            n_c_done = n_c_done + 1; in_c <= 0;
            if (!vx3_aux_req) err("controller aux_done without a request");
        end
    end
    // bridge read: strobe to bridge_rd_data, and how long aux_dout stays after the slot took it
    // (the slot's header assumes only the slot starts aux reads; the copy engine starts them too)
    integer rd_lat_cnt = 0, rd_lat_max = 0;
    reg rd_pending = 0, hold_open = 0;
    realtime t_dout = 0, t_take = 0;
    real dout_setup_min = 1e9, dout_hold_min = 1e9;
    always @(posedge clk_74a) begin
        if (memcard_slot.rd_strobe && !memcard_slot.rst_74 && memcard_slot.req_accept) begin rd_pending <= 1; rd_lat_cnt <= 1; end
        else if (rd_pending) begin
            rd_lat_cnt <= rd_lat_cnt + 1;
            if (memcard_slot.rdv_edge) begin
                rd_pending <= 0;
                if (rd_lat_cnt > rd_lat_max) rd_lat_max = rd_lat_cnt;
            end
        end
        if (memcard_slot.rdv_edge) begin
            t_take = $realtime; hold_open = 1;
            if ($realtime - t_dout < dout_setup_min) dout_setup_min = $realtime - t_dout;
        end
    end
    always @(ss_aux_dout) begin
        t_dout = $realtime;
        if (hold_open) begin
            hold_open = 0;
            if ($realtime - t_take < dout_hold_min) dout_hold_min = $realtime - t_take;
        end
    end
    // how long the slot's one request register stays taken (a strobe that finds it taken is dropped)
    integer pcnt = 0, pend_max = 0, ovf_case = 0, ovf_level = -1, cur_level = -1;
    realtime ovf_t = 0;
    always @(posedge clk_74a) begin
        if (memcard_slot.req_accept) pcnt <= 1;
        else if (memcard_slot.pending) begin pcnt <= pcnt + 1; if (pcnt + 1 > pend_max) pend_max = pcnt + 1; end
    end
    always @(posedge memcard_overflow) if (!probe) begin
        ovf_case = cur_case; ovf_level = cur_level; ovf_t = $realtime;
        $display("ERROR case %0d level %0d t=%0t slot overflow rose: a bridge strobe arrived while the previous access was pending (dropped)", cur_case, cur_level, $realtime);
        err("slot overflow rose");
    end
    // data (not configuration) accesses per die, at chip 0's pins
    integer die0_acc = 0, die1_acc = 0;
    always @(negedge d0_ce0) if (!d0_cre) die0_acc = die0_acc + 1;
    always @(negedge d0_ce1) if (!d0_cre) die1_acc = die1_acc + 1;

    // ---------------- random streams (one per driver) ----------------
    integer bs, gs, ds, is;
    function automatic integer brand(input integer n); brand = $urandom(bs) % n; endfunction
    function automatic integer grand(input integer n); grand = $urandom(gs) % n; endfunction
    function automatic integer drand(input integer n); drand = $urandom(ds) % n; endfunction

    // ---------------- bridge driver (the Pocket; tb_memcard_slot_vram's transactions) ----------------
    reg pend = 0; reg [31:0] pend_exp = 0, pend_addr = 0;
    integer rd_checked = 0, rd_bad = 0, probe_bad = 0;
    reg probe = 0;   // the park-transition probe counts wrong answers without failing the run
    task automatic rd_check;
        begin
            if (pend) begin
                rd_checked = rd_checked + 1;
                if (probe) begin if (bridge_rd_data_top !== pend_exp) probe_bad = probe_bad + 1; end
                else if (bridge_rd_data_top !== pend_exp) begin
                    rd_bad = rd_bad + 1;
                    if (rd_bad <= 4) $display("ERROR case %0d bridge read %h got %h want %h", cur_case, pend_addr, bridge_rd_data_top, pend_exp);
                    err("bridge read data wrong");
                end
            end
            pend = 0;
        end
    endtask
    // core_top's read mux for the two windows
    wire [31:0] bridge_rd_data_top = (bridge_addr[31:17] == 15'h1000 || bridge_addr[31:22] == 10'h0C0) ? memcard_rd_data : 32'd0;
    task automatic apf_write(input [31:0] addr, input [31:0] data, input integer extra);
        begin
            @(posedge clk_74a); bridge_addr <= addr;
            repeat (30) @(posedge clk_74a);
            bridge_wr_data <= data;
            @(posedge clk_74a); bridge_wr <= 1;
            @(posedge clk_74a); bridge_wr <= 0; n_bridge = n_bridge + 1;
            repeat (wper - 33 + extra) @(posedge clk_74a);
        end
    endtask
    // a read transaction samples the answer to the previous read, then strobes
    task automatic apf_read(input [31:0] addr, input [31:0] exp, input integer extra);
        begin
            @(posedge clk_74a); bridge_addr <= addr;
            repeat (RD_SAMPLE - 1) @(posedge clk_74a);
            @(posedge clk_74a); rd_check();
            bridge_rd <= 1; pend = 1; pend_exp = exp; pend_addr = addr;
            @(posedge clk_74a); bridge_rd <= 0; n_bridge = n_bridge + 1;
            repeat (wper - RD_SAMPLE - 2 + extra) @(posedge clk_74a);
        end
    endtask
    task automatic apf_flush;   // the sample point of one more read transaction
        begin
            @(posedge clk_74a);
            repeat (RD_SAMPLE) @(posedge clk_74a);
            rd_check();
        end
    endtask
    task automatic card_wr(input integer i, input [31:0] d, input integer extra);
        begin gold[card_k(i)] = d; apf_write(32'h2000_0000 + 4 * i, d, extra); end
    endtask
    task automatic card_rd(input integer i, input integer extra);
        apf_read(32'h2000_0000 + 4 * i, gold[card_k(i)], extra);
    endtask
    task automatic save_wr(input integer i, input [31:0] d, input integer extra);
        begin
            if (save_k(i) < 0) give_up("bench: save word outside the modelled windows");
            gold[save_k(i)] = d; apf_write(32'h3000_0000 + 4 * i, d, extra);
        end
    endtask
    task automatic save_rd(input integer i, input integer extra);
        apf_read(32'h3000_0000 + 4 * i, gold[save_k(i)], extra);
    endtask
    task automatic card_traffic;   // one random card read or write at a random spacing
        card_traffic_x(brand(4) == 0 ? brand(120) : 0);
    endtask
    task automatic card_traffic_x(input integer x);
        integer i;
        begin
            i = brand(8) == 0 ? (brand(2) ? 0 : CARD_WORDS - 1) : brand(CARD_WORDS);
            if (brand(100) < 40) card_wr(i, $urandom(bs), x); else card_rd(i, x);
        end
    endtask

    // ---------------- guest driver: EXP1 bus (memorymux's clk1x registers), page copies only ----------------
    reg [7:0] pagebuf [0:2047];
    integer copy_wrong = 0, copy_words = 0, copy_cyc_max = 0;
    task automatic bus_wr(input [12:0] a, input [7:0] d);
        begin
            @(posedge clk1x); save_exp1_addr <= a; save_exp1_dataWrite <= d; save_exp1_write <= 1;
            @(posedge clk1x); save_exp1_write <= 0;
        end
    endtask
    // dir 0: staging page -> buffer page; dir 1: buffer page -> staging page
    task automatic do_copy(input integer page, input integer dir);
        integer i, t, k, wrong;
        reg [7:0] b; reg [31:0] w;
        begin
            wrong = 0;
            if (save_k(page * 512) < 0) give_up("bench: page outside the modelled windows");
            if (dir == 0) for (i = 0; i < 2048; i = i + 1) begin
                b = $urandom(gs); pagebuf[i] = b; bus_wr(13'h1000 + i, b);
            end
            bus_wr(13'h1801, page[7:0]);             // PAGE_LO
            bus_wr(13'h1802, {5'd0, page[10:8]});    // PAGE_HI
            repeat (grand(300)) @(posedge clk1x);
            if (dir == 0) for (i = 0; i < 512; i = i + 1)
                gold[save_k(page * 512 + i)] = {pagebuf[4*i+3], pagebuf[4*i+2], pagebuf[4*i+1], pagebuf[4*i]};
            bus_wr(13'h1800, dir ? 8'h03 : 8'h01);   // COMMAND: start, direction
            if (!save_exp1_dataRead[0]) err("STATUS not busy after COMMAND");
            t = 0;
            while (save_exp1_dataRead[0] && t < 400000) begin @(posedge clk1x); t = t + 1; end
            if (save_exp1_dataRead[0]) give_up("page copy did not finish");
            if (t > copy_cyc_max) copy_cyc_max = t;
            copies = copies + 1;
            if (dir == 0) begin
                // the words must be in the chips now (nothing else writes this page)
                repeat (4) @(posedge clk1x);
                for (i = 0; i < 512; i = i + 1) begin
                    k = save_k(page * 512 + i);
                    if ({chip1.mem[k], chip0.mem[k]} !== gold[k]) wrong = wrong + 1;
                end
            end else begin
                @(posedge clk1x); save_exp1_addr <= 13'h1000;
                for (i = 0; i < 2048; i = i + 1) begin
                    @(posedge clk1x); b = save_exp1_dataRead; save_exp1_addr <= 13'h1000 + ((i + 1) % 2048);
                    w = gold[save_k(page * 512 + i / 4)];
                    if (b !== w[8 * (i % 4) +: 8]) wrong = wrong + 1;
                    pagebuf[i] = b;
                end
                @(posedge clk1x); save_exp1_addr <= 13'h1800;
                @(posedge clk1x);
            end
            copy_words = copy_words + 512;
            if (wrong) begin
                copy_wrong = copy_wrong + wrong;
                $display("ERROR case %0d page copy %0s page %0d: %0d of %0d wrong", cur_case,
                    dir ? "buffer->page" : "page->buffer", page, wrong, dir ? 2048 : 512);
                err("page copy data wrong");
            end
        end
    endtask
    function automatic integer pick_page(input integer lo_min);   // a modelled page: lo_min..63 or 1984..2047
        integer r;
        begin
            r = grand(64 - lo_min + 64);
            pick_page = r < 64 - lo_min ? lo_min + r : 1984 + (r - (64 - lo_min));
        end
    endfunction

    // ---------------- DDR master: GPU-style bursts on the controller's main port ----------------
    // reads from VRAM words 0..0xFFFF (never written, checked against the golden copy),
    // write streams to VRAM words 0x10000..0x1FEFF (golden updated on acceptance)
    // gpu_mode 1: random mix with gaps (4-128 word reads, 1-64 word write streams);
    // 2: 128-word reads back to back (the next request is up in the clock of the last word);
    // 3: 128-word write streams back to back; 4: 2 and 3 alternating, no idle clock
    reg gpu_on = 0, gpu_idle = 1;
    integer gpu_mode = 1;
    integer gpu_bursts = 0, gpu_rd_words = 0, gpu_wr_words = 0, gpu_bad = 0;
    always begin : ddr_master
        integer n, len, t, wr;
        reg [21:0] w; reg [63:0] d, e;
        if (!gpu_on) begin gpu_idle = 1; @(posedge clk2x); end
        else begin
            gpu_idle = 0;
            case (gpu_mode)
            2: wr = 0;
            3: wr = 1;
            4: wr = gpu_bursts % 2;
            default: wr = drand(3) == 0;
            endcase
            if (wr) begin
                len = gpu_mode == 1 ? 1 + drand(64) : 128; w = 22'h010000 + drand(22'h00FF00 - 128);
                d = {$urandom(ds), $urandom(ds)};
                ddr3_WE <= 1; ddr3_BURSTCNT <= 8'd1; ddr3_ADDR <= {3'd0, w}; ddr3_DIN <= d; ddr3_BE <= 8'hFF; n = 0; t = 0;
                while (n < len) begin
                    @(posedge clk2x); t = t + 1;
                    if (t > 400000) give_up("DDR write not accepted");
                    if (!ddr3_BUSY) begin
                        gold[2 * (w + n)] = d[31:0]; gold[2 * (w + n) + 1] = d[63:32];
                        n = n + 1; d = {$urandom(ds), $urandom(ds)};
                        ddr3_DIN <= d; ddr3_ADDR <= {3'd0, w} + n;
                    end
                end
                ddr3_WE <= 0; gpu_wr_words = gpu_wr_words + len;
            end else begin
                if (gpu_mode == 1) case (drand(4)) 0: len = 4; 1: len = 16; 2: len = 64; default: len = 128; endcase
                else len = 128;
                w = drand(22'h010000 - 256);
                ddr3_RD <= 1; ddr3_BURSTCNT <= len; ddr3_ADDR <= {3'd0, w}; t = 0;
                @(posedge clk2x);
                while (ddr3_BUSY) begin @(posedge clk2x); t = t + 1; if (t > 400000) give_up("DDR read not accepted"); end
                ddr3_RD <= 0; n = 0; t = 0;
                while (n < len) begin
                    @(posedge clk2x); t = t + 1;
                    if (t > 400000) give_up("DDR read data missing");
                    if (ddr3_DOUT_READY) begin
                        e = {gold[2 * (w + n) + 1], gold[2 * (w + n)]};
                        if (ddr3_DOUT !== e) begin gpu_bad = gpu_bad + 1; err("DDR read data wrong"); end
                        n = n + 1;
                    end
                end
                gpu_rd_words = gpu_rd_words + len;
            end
            gpu_bursts = gpu_bursts + 1;
            if (gpu_mode == 1) repeat (drand(4) == 0 ? drand(400) : drand(8)) @(posedge clk2x);
        end
    end

    // ---------------- helpers ----------------
    task automatic wait_state(input [2:0] s, input integer limit, output real ns);
        integer t; realtime t0;
        begin
            t = 0; t0 = $realtime;
            while (vram.st !== s && t < limit) begin @(posedge clk3x); t = t + 1; end
            if (vram.st !== s) give_up("controller did not reach the expected state");
            ns = $realtime - t0;
        end
    endtask
    task automatic quiesce;
        integer t;
        begin
            gpu_on = 0; t = 0;
            while (!gpu_idle && t < 800000) begin @(posedge clk2x); t = t + 1; end
            repeat (3000) @(posedge clk2x);
            if (mc_aux_req || ss_aux_req || vx3_aux_req) err("aux request still up after the case");
        end
    endtask
    task automatic check_counts;
        begin
            if (n_bridge != n_up_req || n_up_req != n_up_done) begin
                $display("ERROR case %0d bridge transactions %0d, slot aux requests %0d, done pulses to the slot %0d",
                    cur_case, n_bridge, n_up_req, n_up_done);
                err("card-side request lost or duplicated");
            end
            if (n_own_done != 512 * copies) begin
                $display("ERROR case %0d copy done pulses %0d, expected %0d", cur_case, n_own_done, 512 * copies);
                err("copy-side request lost or duplicated");
            end
            if (n_ss_done != n_up_done + n_own_done || n_c_done != n_ss_done || n_c_req != n_c_done) begin
                $display("ERROR case %0d done pulses: bridge side of x3 %0d, slot %0d + copy %0d, controller %0d of %0d requests",
                    cur_case, n_ss_done, n_up_done, n_own_done, n_c_done, n_c_req);
                err("aux done pulses do not add up");
            end
            if (n_cw2 != 0 || n_cw3 != 0) err("card_write pulsed");
            if (memcard_overflow) err("slot overflow (a bridge transaction arrived while one was pending)");
            if (rd_lat_max > wper - 2) err("bridge read answer later than the next transaction");
        end
    endtask
    task automatic end_case(input [8*1400-1:0] detail);
        begin
            $display("CASE %0d %0s %0s", cur_case, case_errors == 0 ? "PASS" : "FAIL", detail);
            if (case_errors != 0 && !cont || cur_case == last_case && cur_case != 8) begin
                $display("CARD_CHAIN %0s", errors == 0 ? "PASS" : "FAIL"); $finish;
            end
            case_errors = 0;
        end
    endtask

    // ---------------- cases 6/7: save-window uploads and readbacks per VRAM traffic level ----------------
    // regions: start, middle and end of the 4 MiB buffer (16,640 save words each)
    function automatic integer region_base(input integer r);
        region_base = r == 0 ? 0 : r == 1 ? 32'h7C000 : 32'hFBF00;
    endfunction
    integer first_drop_level = -1, lvl_fail = 0;
    reg stop_copies = 0;
    task automatic run_level(input integer rd, input [7:0] phase, input integer level, input integer off, input integer cnt,
                             input integer jit, input integer mix);
        integer r, i, w, b0, u0, r0, c0, g0, g1, wrong, dropped, e0, k0;
        begin
            cur_level = level; gpu_mode = level; gpu_on = level != 0;
            repeat (400) @(posedge clk2x);
            @(posedge clk_74a);
            lat_max = 0; lat_sum = 0; lat_n = 0; pend_max = 0; rd_lat_max = 0;
            b0 = n_bridge; u0 = n_up_req; r0 = rd_bad; c0 = rd_checked; g0 = gpu_rd_words; g1 = gpu_wr_words; e0 = case_errors; k0 = copies;
            for (r = 0; r < 3; r = r + 1) for (i = 0; i < cnt; i = i + 1) begin
                w = region_base(r) + off + i;
                if (rd) save_rd(w, jit ? brand(9) : 0); else save_wr(w, $urandom(bs), jit ? brand(9) : 0);
                if (mix && brand(4) == 0) card_traffic_x(jit ? brand(9) : 0);
            end
            if (rd) apf_flush();
            repeat (300) @(posedge clk2x);
            wrong = 0;
            if (!rd) for (r = 0; r < 3; r = r + 1) for (i = 0; i < cnt; i = i + 1) begin
                w = save_k(region_base(r) + off + i);
                if ({chip1.mem[w], chip0.mem[w]} !== gold[w]) wrong = wrong + 1;
            end
            dropped = (n_bridge - b0) - (n_up_req - u0);
            if (wrong != 0) err("uploaded save words not on die 1");
            if (dropped != 0) err("bridge transactions dropped by the slot");
            if (pend_max > wper - 1) err("slot request still pending at the next strobe");
            if ((dropped != 0 || memcard_overflow) && first_drop_level < 0) first_drop_level = level;
            if (case_errors != e0) lvl_fail = lvl_fail + 1;
            $display("LEVEL %0d %0s {\"phase\":\"%0s\",\"level\":%0d,\"spacing\":\"%0s\",\"card_and_copies\":%0d,\"save_words\":%0d,\"bridge_tx\":%0d,\"dropped\":%0d,\"overflow\":%0d,\"words_wrong_die1\":%0d,\"reads_checked\":%0d,\"reads_wrong\":%0d,\"aux_latency_max_clk2x\":%0d,\"aux_latency_mean_clk2x\":%0.2f,\"slot_pending_max_clk74\":%0d,\"read_strobe_to_data_max_clk74\":%0d,\"gpu_read_words\":%0d,\"gpu_write_words\":%0d,\"page_copies\":%0d}",
                cur_case, case_errors == e0 ? "PASS" : "FAIL", phase, level, jit ? "88..96" : "88", mix, 3 * cnt, n_bridge - b0, dropped, memcard_overflow, wrong,
                rd_checked - c0, rd_bad - r0, lat_max, lat_n ? 1.0 * lat_sum / lat_n : 0.0, pend_max, rd_lat_max,
                gpu_rd_words - g0, gpu_wr_words - g1, copies - k0);
            if (case_errors != e0 && !cont) begin
                $display("CASE %0d FAIL {\"first_drop_level\":%0d,\"stopped_at_phase\":\"%0s\",\"stopped_at_level\":%0d}", cur_case, first_drop_level, phase, level);
                $display("CARD_CHAIN FAIL"); $finish;
            end
        end
    endtask
    // phase A: exact 88-clock spacing, bridge save-window traffic only; J: spacing 88..96;
    // B: exact spacing with a card access after every fourth save word (on average) and page copies running
    task automatic run_levels(input integer rd, input integer na, input integer nj, input integer nb);
        integer l;
        begin
            for (l = 0; l <= 4; l = l + 1) run_level(rd, "A", l, l * na, na, 0, 0);
            for (l = 0; l <= 4; l = l + 1) run_level(rd, "J", l, l * nj, nj, 1, 0);
            stop_copies = 0;
            fork
                begin
                    // copies use pages the bridge does not touch here: 40..63 and 1984..2010
                    while (!stop_copies) do_copy(grand(2) ? 40 + grand(24) : 1984 + grand(27), grand(2));
                end
                begin
                    for (l = 0; l <= 4; l = l + 1) run_level(rd, "B", l, l * nb, nb, 0, 1);
                    stop_copies = 1;
                end
            join
            cur_level = -1; gpu_mode = 1;
        end
    endtask

    // ---------------- card-holdoff: the hold-off's reference model and case 9 ----------------
`ifdef SLOT_HOLDOFF
    localparam integer HQ = (1 << `SLOT_HOLDOFF) - 1;
`else
    localparam integer HQ = 1023;   // the gaps of case 9 are drawn with the same quiet time
`endif
    integer hq = 0, park_changes = 0, park_deferred = 0;
    reg h_sat, h_req, h_pre;
    realtime t_toggle = -1e9;
    always @(reset_n) t_toggle = $realtime;
`ifdef SLOT_HOLDOFF
    // park follows !reset_n at the clk_74a edge that finds the windows quiet for HQ clocks (quiet_74
    // saturated); otherwise it keeps its value. A strobe clears the count.
    always @(posedge clk_74a) begin
        h_pre = vram_park; h_req = !reset_n; h_sat = hq == HQ;
        hq = (memcard_slot.wr_strobe || memcard_slot.rd_strobe) ? 0 : h_sat ? hq : hq + 1;
        #0.1;
        if (vram_park !== (h_sat ? h_req : h_pre)) err("park differs from the hold-off model");
        if (vram_park !== h_pre) park_changes = park_changes + 1;
        if (h_sat && h_req !== (h_pre === 1'b1) && $realtime - t_toggle > 100.0) park_deferred = park_deferred + 1;
    end
`endif
    // case 9: bursts of 8..47 bridge card accesses (3 of 4 reads) at 88..96 clk_74a with pauses
    // shorter than HQ / 2 or longer than HQ + 300, while the core reset toggles every 200..2,700
    // clk_74a. As the Pocket sends a reset command and then polls it, no burst starts within 3 us
    // after a reset change. Gating: every read answer right, no slot overflow, every strobe reaches
    // the slot, card contents equal the golden copy, the controller follows the hold-off model and
    // ends unparked. Then (reported, not gating) the residual: with the windows quiet for HQ, a
    // reset change and a burst 0..60 clk_74a after it.
    integer h_bursts = 0, h_toggles = 0, h_acc = 0, h_res_wrong = 0, h_res_ovf = 0;
    reg h_done = 0;
    task automatic holdoff_case;
        integer b, n, x, e0, r0, ck0, ov;
        begin
            cur_case = 9; probe = 0; e0 = errors; r0 = rd_bad; ck0 = rd_checked;
            fork
                begin
                    while (!h_done) begin
                        repeat (200 + grand(2500)) @(posedge clk_74a);
                        if (!h_done) begin reset_n <= !reset_n; h_toggles = h_toggles + 1; end
                    end
                end
                begin
                    for (b = 0; b < holdoff_n; b = b + 1) begin
                        while ($realtime - t_toggle < 3000.0) @(posedge clk_74a);
                        n = 8 + brand(40);
                        for (x = 0; x < n; x = x + 1) begin
                            if (brand(4) == 0) card_wr(brand(CARD_WORDS), $urandom(bs), brand(9));
                            else card_rd(brand(CARD_WORDS), brand(9));
                            h_acc = h_acc + 1;
                        end
                        apf_flush();
                        h_bursts = h_bursts + 1;
                        repeat (brand(2) ? brand(HQ / 2) : HQ + 300 + brand(HQ)) @(posedge clk_74a);
                    end
                    h_done = 1;
                end
            join
            @(posedge clk_74a); reset_n <= 1;
            repeat (HQ + 200) @(posedge clk_74a);
            wait_state(3'd2, 400000, park_ns);
            quiesce(); compare_all();
            ov = memcard_overflow;
            if (n_bridge != n_up_req || n_up_req != n_up_done) err("card-side request lost or duplicated");
`ifdef SLOT_HOLDOFF
            if (park_changes < 6) err("too few park changes reached the controller");
`endif
            $display("HOLDOFF {\"bursts\":%0d,\"accesses\":%0d,\"reset_toggles\":%0d,\"park_changes\":%0d,\"park_changes_deferred\":%0d,\"reads_checked\":%0d,\"reads_wrong\":%0d,\"dropped\":%0d,\"overflow\":%0d,\"card_words_wrong\":%0d,\"slot_pending_max_clk74\":%0d,\"hold_bits\":%0d}",
                h_bursts, h_acc, h_toggles, park_changes, park_deferred, rd_checked - ck0, rd_bad - r0, n_bridge - n_up_req, ov, bad_card, pend_max,
`ifdef SLOT_HOLDOFF
                `SLOT_HOLDOFF);
`else
                0);
`endif
            $display("CASE 9 %0s", errors == e0 ? "PASS" : "FAIL");
`ifdef SLOT_HOLDOFF
            // residual (not gating): quiet for HQ, a reset change, a burst right after it
            if (errors == e0) begin
                probe = 1; probe_bad = 0; ck0 = rd_checked;
                for (b = 0; b < 12; b = b + 1) begin
                    repeat (HQ + 300) @(posedge clk_74a);
                    reset_n <= !reset_n;
                    repeat (2 + brand(60)) @(posedge clk_74a);
                    for (x = 0; x < 8; x = x + 1) card_rd(brand(CARD_WORDS), 0);
                    apf_flush();
                end
                @(posedge clk_74a); reset_n <= 1; repeat (HQ + 200) @(posedge clk_74a);
                $display("RESIDUAL {\"transitions\":12,\"reads_checked\":%0d,\"reads_wrong\":%0d,\"slot_overflow\":%0d}",
                    rd_checked - ck0, probe_bad, memcard_overflow);
            end
`endif
            $display("CARD_CHAIN %0s", errors == 0 ? "PASS" : "FAIL");
        end
    endtask

    // ---------------- the cases ----------------
    reg [31:0] card_img [0:CARD_WORDS-1];
    reg [31:0] s8_file [0:16*512-1];
    integer s8_bad = 0;
    function automatic integer s8_page(input integer j);
        s8_page = j < 8 ? j : j < 12 ? 1000 + (j - 8) : 2044 + (j - 12);
    endfunction
    reg guest_done = 0, bridge_done = 0;
    real park_ns, unpark_ns, park2_ns, unpark2_ns, init_ns;
    integer i, j, k, t, d0a, d1a, c0, w2, rdc0;
    reg [8*1400-1:0] line;
    initial begin
        #1;
        // the middle save window is not in the chip model's own seeding
        for (k = 2 * 32'h84000; k < 2 * VP_WORDS; k = k + 1) begin
            chip0.mem[k] = vp_seed(0, 22'h23E000 + (k / 2 - 32'h84000), k % 2);
            chip1.mem[k] = vp_seed(1, 22'h23E000 + (k / 2 - 32'h84000), k % 2);
        end
        for (k = 0; k < 2 * VP_WORDS; k = k + 1) gold[k] = {chip1.mem[k], chip0.mem[k]};   // seeded contents
        bs = seed * 7919 + 1; gs = seed * 104729 + 2; ds = seed * 1299709 + 3; is = 32'h4D43_0001;
        for (i = 0; i < CARD_WORDS; i = i + 1) card_img[i] = $urandom(is);
        card_img[0][15:0] = 16'h434D;   // 'M','C'
        repeat (20) @(posedge clk2x); cpu_locked <= 1;

        // ---- case 1: card load while parked
        cur_case = 1;
        wait_state(3'd5, 400000, init_ns);
        for (i = 0; i < card_n; i = i + 1) begin
            card_wr(i, card_img[i], 0);
            if (i == card_n - 1) memcard_load_done <= 1;
        end
        t = 0; while (mc_busy && t < 200000) begin @(posedge clk2x); t = t + 1; end
        if (mc_busy) give_up("slot busy did not fall after the load");
        repeat (4) @(posedge clk2x);
        compare_all();
        c0 = bad_card; w2 = bad_d0 + bad_save + bad_d1;
        for (i = 0; i < card_n; i = i + 1) if (gold[card_k(i)] !== card_img[i]) err("bench: golden card differs from the image");
        if (die1_acc != 0) err("die 1 selected during the parked card load");
        if (die0_acc != card_n) err("die 0 accesses differ from the words loaded");
        d0a = die0_acc; d1a = die1_acc;
        @(posedge clk_74a); reset_n <= 1;
        wait_state(3'd2, 400000, unpark_ns);
        for (i = 0; i < card_n; i = i + 1) card_rd(i, 0);
        apf_flush();
        rdc0 = rd_bad;
        quiesce(); compare_all(); check_counts();
        if (die1_acc != 0) err("die 1 selected during the card readback");
        $sformat(line, "{\"card_words_loaded\":%0d,\"after_load_card_words_wrong_die0\":%0d,\"after_load_other_words_changed\":%0d,\"die0_accesses_load\":%0d,\"die1_accesses_load\":%0d,\"readback_words\":%0d,\"readback_wrong\":%0d,\"after_readback_card_wrong\":%0d,\"after_readback_other_changed\":%0d,\"die1_accesses_total\":%0d,\"init_to_parked_ns\":%0.0f,\"unpark_ns\":%0.0f,\"slot_aux_latency_max_clk2x\":%0d,\"read_strobe_to_data_max_clk74\":%0d,\"bridge_tx\":%0d,\"slot_done\":%0d}",
            card_n, c0, w2, d0a, d1a, rd_checked, rdc0, bad_card, bad_d0 + bad_save + bad_d1, die1_acc, init_ns, unpark_ns, up_lat_max, rd_lat_max, n_bridge, n_up_done);
        end_case(line);

        if (holdoff_n > 0) begin holdoff_case(); $finish; end
        // ---- case 2: state written while parked (a wake)
        cur_case = 2; up_lat_max = 0; rd_lat_max = 0; rd_checked = 0; rd_bad = 0;
        @(posedge clk_74a); reset_n <= 0;
        wait_state(3'd5, 400000, park_ns);
        d0a = die0_acc; d1a = die1_acc;
        is = 32'h5354_0002;
        // first 64 KiB, last 64 KiB: byte addresses whose low 17 bits cover every card address once
        for (i = 0; i < save_n; i = i + 1) save_wr(i, $urandom(is), 0);
        for (i = 0; i < save_n; i = i + 1) save_wr(32'hFC000 + i, $urandom(is), 0);
        repeat (40) @(posedge clk_74a);
        d0a = die0_acc - d0a; d1a = die1_acc - d1a;
        if (d0a != 0) err("die 0 selected by parked save-window writes");
        if (d1a != 2 * save_n) err("die 1 accesses differ from the save words written");
        compare_all();
        c0 = bad_card; w2 = bad_save; k = bad_d0 + bad_d1;
        for (i = 0; i < card_n; i = i + 1) if (gold[card_k(i)] !== card_img[i]) err("bench: golden card differs from the image");
        // parked reads of both windows
        for (i = 0; i < 256; i = i + 1) begin
            j = brand(2 * save_n); save_rd(j < save_n ? j : 32'hFC000 + j - save_n, 0);
            card_rd(i < 2 ? (i ? CARD_WORDS - 1 : 0) : brand(CARD_WORDS), 0);
        end
        apf_flush();
        @(posedge clk_74a); reset_n <= 1;
        wait_state(3'd2, 400000, unpark2_ns);
        quiesce(); compare_all(); check_counts();
        $sformat(line, "{\"save_words_written_parked\":%0d,\"save_words_wrong_die1\":%0d,\"card_words_changed_die0\":%0d,\"other_words_changed\":%0d,\"die0_accesses_during_writes\":%0d,\"die1_accesses_during_writes\":%0d,\"parked_reads\":%0d,\"parked_reads_wrong\":%0d,\"card_write_pulses_clk3x\":%0d,\"card_write_pulses_clk2x\":%0d,\"park_ns\":%0.0f,\"unpark_ns\":%0.0f,\"slot_aux_latency_max_clk2x\":%0d,\"read_strobe_to_data_max_clk74\":%0d,\"end_card_wrong\":%0d,\"end_save_wrong\":%0d,\"bridge_tx\":%0d,\"slot_done\":%0d}",
            2 * save_n, w2, c0, k, d0a, d1a, rd_checked, rd_bad, n_cw3, n_cw2, park_ns, unpark2_ns, up_lat_max, rd_lat_max, bad_card, bad_save, n_bridge, n_up_done);
        end_case(line);

        // ---- case 3: running, page copies against bridge card traffic
        cur_case = 3; up_lat_max = 0; rd_lat_max = 0; rd_checked = 0; rd_bad = 0; c0 = n_bridge;
        dout_setup_min = 1e9; dout_hold_min = 1e9; hold_open = 0;
        gpu_on = gpu != 0; guest_done = 0;
        fork
            begin
                // page 0 and the top page both ways, then random modelled pages
                do_copy(0, 0); do_copy(2047, 0); do_copy(0, 1); do_copy(2047, 1);
                for (j = 4; j < n3; j = j + 1) do_copy(pick_page(0), grand(2));
                guest_done = 1;
            end
            begin
                while (!guest_done) card_traffic();
                apf_flush();
            end
        join
        quiesce(); compare_all(); check_counts();
        $sformat(line, "{\"page_copies\":%0d,\"copy_words\":%0d,\"copy_words_wrong\":%0d,\"copy_done_pulses\":%0d,\"copy_clk1x_max\":%0d,\"bridge_card_tx\":%0d,\"card_reads_checked\":%0d,\"card_reads_wrong\":%0d,\"bridge_tx\":%0d,\"slot_requests\":%0d,\"slot_done\":%0d,\"x3_done_clk2x\":%0d,\"controller_done_clk3x\":%0d,\"both_granted\":%0d,\"orphan_done\":%0d,\"end_card_wrong\":%0d,\"end_save_wrong\":%0d,\"end_other_wrong\":%0d,\"gpu_bursts\":%0d,\"gpu_read_words\":%0d,\"gpu_read_wrong\":%0d,\"gpu_write_words\":%0d,\"slot_aux_latency_max_clk2x\":%0d,\"read_strobe_to_data_max_clk74\":%0d,\"aux_dout_stable_before_take_min_ns\":%0.1f,\"aux_dout_held_after_take_min_ns\":%0.1f}",
            copies, copy_words, copy_wrong, n_own_done, copy_cyc_max, n_bridge - c0, rd_checked, rd_bad, n_bridge, n_up_req, n_up_done, n_ss_done, n_c_done,
            n_both, n_orphan, bad_card, bad_save, bad_d0 + bad_d1, gpu_bursts, gpu_rd_words, gpu_bad, gpu_wr_words, up_lat_max, rd_lat_max, dout_setup_min, dout_hold_min);
        end_case(line);

        // ---- case 4: running, the Pocket reads the state out; card traffic and page copies go on
        cur_case = 4; up_lat_max = 0; rd_lat_max = 0; rd_checked = 0; rd_bad = 0; c0 = n_bridge; k = copies; copy_cyc_max = 0;
        dout_setup_min = 1e9; dout_hold_min = 1e9; hold_open = 0;
        gpu_on = gpu != 0; bridge_done = 0;
        fork
            begin
                // copies into the buffer stay off the pages the bridge reads; copies out read any page
                while (!bridge_done) begin
                    if (grand(2)) do_copy(pick_page((n4 + 511) / 512), 0); else do_copy(pick_page(0), 1);
                end
            end
            begin
                for (j = 0; j < n4; j = j + 1) begin
                    save_rd(j, brand(8) == 0 ? brand(60) : 0);
                    if (brand(3) == 0) card_traffic();
                end
                apf_flush();
                bridge_done = 1;
            end
        join
        quiesce(); compare_all(); check_counts();
        $sformat(line, "{\"save_words_read\":%0d,\"bridge_tx_case\":%0d,\"reads_checked\":%0d,\"reads_wrong\":%0d,\"page_copies\":%0d,\"copy_words_wrong_total\":%0d,\"copy_done_pulses\":%0d,\"copy_clk1x_max\":%0d,\"bridge_tx\":%0d,\"slot_requests\":%0d,\"slot_done\":%0d,\"x3_done_clk2x\":%0d,\"controller_done_clk3x\":%0d,\"both_granted\":%0d,\"orphan_done\":%0d,\"end_card_wrong\":%0d,\"end_save_wrong\":%0d,\"end_other_wrong\":%0d,\"gpu_bursts\":%0d,\"gpu_read_words\":%0d,\"gpu_read_wrong\":%0d,\"gpu_write_words\":%0d,\"card_write_pulses\":%0d,\"slot_aux_latency_max_clk2x\":%0d,\"read_strobe_to_data_max_clk74\":%0d,\"aux_dout_stable_before_take_min_ns\":%0.1f,\"aux_dout_held_after_take_min_ns\":%0.1f}",
            n4, n_bridge - c0, rd_checked, rd_bad, copies - k, copy_wrong, n_own_done, copy_cyc_max, n_bridge, n_up_req, n_up_done, n_ss_done, n_c_done,
            n_both, n_orphan, bad_card, bad_save, bad_d0 + bad_d1, gpu_bursts, gpu_rd_words, gpu_bad, gpu_wr_words, n_cw2 + n_cw3, up_lat_max, rd_lat_max, dout_setup_min, dout_hold_min);
        end_case(line);

        // ---- case 6: uploads to the save window at APF spacing per VRAM traffic level
        cur_case = 6; up_lat_max = 0; rd_checked = 0; rd_bad = 0; c0 = n_bridge; k = copies; lvl_fail = 0;
        run_levels(0, up_n, 512, 1024);
        quiesce(); compare_all(); check_counts();
        $sformat(line, "{\"levels_failed\":%0d,\"first_drop_level\":%0d,\"bridge_tx_case\":%0d,\"aux_latency_max_clk2x\":%0d,\"page_copies\":%0d,\"copy_words_wrong_total\":%0d,\"overflow\":%0d,\"bridge_tx\":%0d,\"slot_requests\":%0d,\"slot_done\":%0d,\"end_card_wrong\":%0d,\"end_save_wrong\":%0d,\"end_other_wrong\":%0d,\"gpu_read_wrong\":%0d}",
            lvl_fail, first_drop_level, n_bridge - c0, up_lat_max, copies - k, copy_wrong, memcard_overflow, n_bridge, n_up_req, n_up_done, bad_card, bad_save, bad_d0 + bad_d1, gpu_bad);
        end_case(line);

        // ---- case 7: readbacks of the save window at APF spacing per VRAM traffic level
        cur_case = 7; up_lat_max = 0; rd_checked = 0; rd_bad = 0; c0 = n_bridge; k = copies; lvl_fail = 0;
        run_levels(1, rb_n, 512, 1024);
        quiesce(); compare_all(); check_counts();
        $sformat(line, "{\"levels_failed\":%0d,\"first_drop_level\":%0d,\"bridge_tx_case\":%0d,\"reads_checked\":%0d,\"reads_wrong\":%0d,\"aux_latency_max_clk2x\":%0d,\"page_copies\":%0d,\"copy_words_wrong_total\":%0d,\"overflow\":%0d,\"bridge_tx\":%0d,\"slot_requests\":%0d,\"slot_done\":%0d,\"end_card_wrong\":%0d,\"end_save_wrong\":%0d,\"end_other_wrong\":%0d,\"gpu_read_wrong\":%0d}",
            lvl_fail, first_drop_level, n_bridge - c0, rd_checked, rd_bad, up_lat_max, copies - k, copy_wrong, memcard_overflow, n_bridge, n_up_req, n_up_done, bad_card, bad_save, bad_d0 + bad_d1, gpu_bad);
        end_case(line);

        // ---- case 8: a save, the Pocket's read-out, the buffer overwritten, the Pocket's upload, a load
        cur_case = 8; up_lat_max = 0; rd_lat_max = 0; pend_max = 0; rd_checked = 0; rd_bad = 0; c0 = n_bridge; k = copies; w2 = copy_wrong;
        // 16 pages: 0..7, 1000..1003 (middle), 2044..2047
        gpu_mode = 1; gpu_on = gpu != 0;
        for (j = 0; j < 16; j = j + 1) begin                    // the save routine stores pattern 1
            do_copy(s8_page(j), 0);
            for (i = 0; i < 512; i = i + 1) s8_file[j * 512 + i] = gold[save_k(s8_page(j) * 512 + i)];
        end
        gpu_mode = 4; cur_level = 4;                            // worst-case VRAM traffic from here
        for (j = 0; j < 16 * 512; j = j + 1) save_rd(s8_page(j / 512) * 512 + j % 512, 0);   // the Pocket reads the state
        apf_flush();
        d0a = rd_bad;
        for (j = 0; j < 16; j = j + 1) do_copy(s8_page(j), 0);  // the buffer gets another pattern
        t = 0;
        for (j = 0; j < 16 * 512; j = j + 1) if (gold[save_k(s8_page(j / 512) * 512 + j % 512)] === s8_file[j]) t = t + 1;
        if (t > 8) err("bench: the second pattern equals the first");
        for (j = 0; j < 16 * 512; j = j + 1) save_wr(s8_page(j / 512) * 512 + j % 512, s8_file[j], 0);   // the Pocket uploads pattern 1
        repeat (300) @(posedge clk2x);
        d1a = copy_wrong;
        for (j = 0; j < 16; j = j + 1) begin                    // the load routine fetches it page by page
            do_copy(s8_page(j), 1);
            for (i = 0; i < 512; i = i + 1)
                if ({pagebuf[4*i+3], pagebuf[4*i+2], pagebuf[4*i+1], pagebuf[4*i]} !== s8_file[j * 512 + i]) s8_bad = s8_bad + 1;
        end
        if (s8_bad != 0) err("pattern 1 did not come back through the page copies");
        cur_level = -1;
        quiesce(); compare_all(); check_counts();
        $sformat(line, "{\"pages\":16,\"state_words\":8192,\"readout_reads_wrong\":%0d,\"upload_words\":8192,\"words_wrong_after_load_copies\":%0d,\"load_copy_words_wrong_vs_golden\":%0d,\"page_copies\":%0d,\"bridge_tx_case\":%0d,\"aux_latency_max_clk2x\":%0d,\"slot_pending_max_clk74\":%0d,\"read_strobe_to_data_max_clk74\":%0d,\"overflow\":%0d,\"bridge_tx\":%0d,\"slot_requests\":%0d,\"slot_done\":%0d,\"end_card_wrong\":%0d,\"end_save_wrong\":%0d,\"end_other_wrong\":%0d,\"gpu_read_wrong\":%0d}",
            d0a, s8_bad, copy_wrong - d1a, copies - k, n_bridge - c0, up_lat_max, pend_max, rd_lat_max, memcard_overflow, n_bridge, n_up_req, n_up_done, bad_card, bad_save, bad_d0 + bad_d1, gpu_bad);
        end_case(line);

        // ---- probe (not a pass/fail case): bridge card reads at the fastest spacing across park and
        // unpark transitions. The slot holds one request; a transition longer than the spacing can
        // make the next strobe arrive while the first still waits (slot overflow: that strobe is dropped).
        if (errors == 0 && probe_n > 0) begin
            probe = 1; rd_checked = 0; c0 = n_bridge; k = n_up_req; guest_done = 0;
            fork
                begin
                    for (j = 0; j < 12; j = j + 1) begin
                        repeat (400 + grand(3000)) @(posedge clk_74a);
                        reset_n <= !reset_n;
                    end
                    repeat (4000) @(posedge clk_74a);
                    reset_n <= 1; guest_done = 1;
                end
                begin
                    while (!guest_done) card_rd(brand(CARD_WORDS), 0);
                    apf_flush();
                end
            join
            repeat (3000) @(posedge clk2x);
            $display("PROBE park-transitions {\"transitions\":12,\"bridge_reads\":%0d,\"reads_reaching_the_slot_aux_port\":%0d,\"answers_checked\":%0d,\"answers_wrong\":%0d,\"slot_overflow\":%0d}",
                n_bridge - c0, n_up_req - k, rd_checked, probe_bad, memcard_overflow);
        end
        $display("CARD_CHAIN %0s", errors == 0 ? "PASS" : "FAIL"); $finish;
    end
    initial begin #3_000_000_000.0; $display("ERROR timeout"); $display("CARD_CHAIN FAIL"); $finish; end
endmodule
`default_nettype wire
