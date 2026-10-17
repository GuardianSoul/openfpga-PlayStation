// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Integration bench: rtl/pocket/psx_memcard_slot.sv on the real VRAM
// controller rtl/memory/psx_vram_psram.sv and two psx_cram_model chips (run
// by scripts/run_memcard_slot.py; skip with --no-integration).
//
// Sequence, as the top will drive it:
//   1. reset, park high (park = APF reset: the core is held in reset while
//      APF loads the slot). The APF load starts once the controller is
//      parked (the top acknowledges slot writes only after memory is ready).
//   2. APF load of all 32,768 words (one transaction per WPER clk_74a),
//      load_done right after the last write; busy must fall with the image
//      (or the formatted image) in the chips' storage.
//   3. unpark (core runs), guest card writes through ddr_we into the card
//      window (DDR byte 0x0100000+, which drives card_write), idle check.
//   4. APF readback in synchronous mode while GPU-style VRAM bursts run
//      (reads, or reads alternating with write streams with GPU_WR=1) and
//      the guest keeps writing card words the readback reaches later (the
//      bench checks each such write lands before its words are read).
//   5. park again (APF quit) and a second, parked (asynchronous) readback.
// Both readbacks are compared byte for byte.
`timescale 1ps/1ps
`default_nettype none
module tb_memcard_slot_vram;
    localparam integer WORDS = 32768;
    localparam integer RD_SAMPLE = 5;
    `include "psx_vram_psram_windows.svh"

    reg [8*16-1:0] image;
    integer park_load, gpu_gap, seed, seed0, wper, rper, phase_ps, guest_n, gpu_burst, gpu_wr, ahead;
    initial begin
        if (!$value$plusargs("IMAGE=%s", image)) image = "real";
        if (!$value$plusargs("SEED=%d", seed)) seed = 1;
        if (!$value$plusargs("WPER=%d", wper)) wper = 88;
        if (!$value$plusargs("RPER=%d", rper)) rper = 88;
        if (!$value$plusargs("PHASE=%d", phase_ps)) phase_ps = 0;
        if (!$value$plusargs("GUEST=%d", guest_n)) guest_n = 300;
        if (!$value$plusargs("GPU_BURST=%d", gpu_burst)) gpu_burst = 128;   // MiSTer GPU scanout/copy bursts
        if (!$value$plusargs("GPU_GAP=%d", gpu_gap)) gpu_gap = 0;           // idle clk2x after each burst
        if (!$value$plusargs("PARK_LOAD=%d", park_load)) park_load = 1;     // 0: load in synchronous mode
        if (!$value$plusargs("GPU_WR=%d", gpu_wr)) gpu_wr = 0;              // 1: alternate read and write bursts
        // a guest write during the readback targets words this many reads
        // ahead: the single DDR master may first finish one GPU burst
        // (~4.2 clk2x per word plus aux interleaving, 80 clk2x per APF read)
        if (!$value$plusargs("AHEAD=%d", ahead)) ahead = 4 + (gpu_burst * 6 + 40) / 80;
        park = park_load != 0;
        seed0 = seed;
    end

    reg clk_74a = 0, clk2x = 0;
    initial forever #6734 clk_74a = !clk_74a;
    initial begin #(phase_ps + 1); forever #7382 clk2x = !clk2x; end

    // ---------------- slot ----------------
    reg [31:0] bridge_addr = 0, bridge_wr_data = 0;
    reg bridge_wr = 0, bridge_rd = 0, load_done = 0, reset = 1, park = 1;
    wire [31:0] bridge_rd_data, aux_din, aux_dout;
    wire [20:0] aux_addr;
    wire overflow, busy, aux_req, aux_we, aux_done, card_write;
    psx_memcard_slot slot (
        .clk_74a(clk_74a), .bridge_addr(bridge_addr), .bridge_wr(bridge_wr),
        .bridge_wr_data(bridge_wr_data), .bridge_rd(bridge_rd), .bridge_rd_data(bridge_rd_data),
        .overflow(overflow), .clk2x(clk2x), .reset_2x(reset), .busy(busy),
        .aux_req(aux_req), .aux_we(aux_we), .aux_addr(aux_addr), .aux_din(aux_din),
        .aux_dout(aux_dout), .aux_done(aux_done), .card_write(card_write), .load_done(load_done));

    // ---------------- VRAM controller + chips (board as in tb_psx_vram_psram) ----------------
    reg [7:0] ddr_burstcnt = 0; reg [24:0] ddr_addr = 0; reg ddr_rd = 0, ddr_we = 0;
    reg [63:0] ddr_din = 0; reg [7:0] ddr_be = 0;
    wire ddr_busy, ddr_dout_ready; wire [63:0] ddr_dout;
    wire [21:16] c0_a, c1_a; wire [15:0] c0_dq_out, c1_dq_out; wire c0_dq_oe, c1_dq_oe;
    wire c0_clk, c1_clk, c0_adv_n, c1_adv_n, c0_cre, c1_cre, c0_ce0_n, c1_ce0_n, c0_ce1_n, c1_ce1_n;
    wire c0_oe_n, c1_oe_n, c0_we_n, c1_we_n, c0_ub_n, c1_ub_n, c0_lb_n, c1_lb_n;
    reg  [15:0] c0_dq_in = 16'hzzzz, c1_dq_in = 16'hzzzz;
    psx_vram_psram vram (
        .clk(clk2x), .reset(reset), .park(park),
        .ddr_busy(ddr_busy), .ddr_burstcnt(ddr_burstcnt), .ddr_addr(ddr_addr), .ddr_dout(ddr_dout),
        .ddr_dout_ready(ddr_dout_ready), .ddr_rd(ddr_rd), .ddr_din(ddr_din), .ddr_be(ddr_be), .ddr_we(ddr_we),
        .aux_req(aux_req), .aux_we(aux_we), .aux_addr(aux_addr), .aux_din(aux_din), .aux_dout(aux_dout),
        .aux_done(aux_done), .card_write(card_write),
        .cram0_a(c0_a), .cram0_dq_out(c0_dq_out), .cram0_dq_oe(c0_dq_oe), .cram0_dq_in(c0_dq_in),
        .cram0_clk(c0_clk), .cram0_adv_n(c0_adv_n), .cram0_cre(c0_cre), .cram0_ce0_n(c0_ce0_n),
        .cram0_ce1_n(c0_ce1_n), .cram0_oe_n(c0_oe_n), .cram0_we_n(c0_we_n), .cram0_ub_n(c0_ub_n), .cram0_lb_n(c0_lb_n),
        .cram1_a(c1_a), .cram1_dq_out(c1_dq_out), .cram1_dq_oe(c1_dq_oe), .cram1_dq_in(c1_dq_in),
        .cram1_clk(c1_clk), .cram1_adv_n(c1_adv_n), .cram1_cre(c1_cre), .cram1_ce0_n(c1_ce0_n),
        .cram1_ce1_n(c1_ce1_n), .cram1_oe_n(c1_oe_n), .cram1_we_n(c1_we_n), .cram1_ub_n(c1_ub_n), .cram1_lb_n(c1_lb_n));

    localparam integer DLY = 1000;   // 1 ns per board group
    reg [5:0] d0_a = 0, d1_a = 0; reg [15:0] d0_dq = 0, d1_dq = 0; reg d0_oe = 0, d1_oe = 0;
    reg d0_clk = 0, d1_clk = 0, d0_adv = 1, d1_adv = 1, d0_cre = 0, d1_cre = 0, d0_ce0 = 1, d1_ce0 = 1;
    reg d0_ce1 = 1, d1_ce1 = 1, d0_oen = 1, d1_oen = 1, d0_wen = 1, d1_wen = 1, d0_ub = 1, d1_ub = 1, d0_lb = 1, d1_lb = 1;
    always @(c0_clk) d0_clk <= #(DLY) c0_clk;            always @(c1_clk) d1_clk <= #(DLY) c1_clk;
    always @(c0_a) d0_a <= #(DLY) c0_a;                  always @(c1_a) d1_a <= #(DLY) c1_a;
    always @(c0_adv_n) d0_adv <= #(DLY) c0_adv_n;        always @(c1_adv_n) d1_adv <= #(DLY) c1_adv_n;
    always @(c0_cre) d0_cre <= #(DLY) c0_cre;            always @(c1_cre) d1_cre <= #(DLY) c1_cre;
    always @(c0_ce0_n) d0_ce0 <= #(DLY) c0_ce0_n;        always @(c1_ce0_n) d1_ce0 <= #(DLY) c1_ce0_n;
    always @(c0_ce1_n) d0_ce1 <= #(DLY) c0_ce1_n;        always @(c1_ce1_n) d1_ce1 <= #(DLY) c1_ce1_n;
    always @(c0_oe_n) d0_oen <= #(DLY) c0_oe_n;          always @(c1_oe_n) d1_oen <= #(DLY) c1_oe_n;
    always @(c0_we_n) d0_wen <= #(DLY) c0_we_n;          always @(c1_we_n) d1_wen <= #(DLY) c1_we_n;
    always @(c0_ub_n) d0_ub <= #(DLY) c0_ub_n;           always @(c1_ub_n) d1_ub <= #(DLY) c1_ub_n;
    always @(c0_lb_n) d0_lb <= #(DLY) c0_lb_n;           always @(c1_lb_n) d1_lb <= #(DLY) c1_lb_n;
    always @(c0_dq_out) d0_dq <= #(DLY) c0_dq_out;       always @(c1_dq_out) d1_dq <= #(DLY) c1_dq_out;
    always @(c0_dq_oe) d0_oe <= #(DLY) c0_dq_oe;         always @(c1_dq_oe) d1_oe <= #(DLY) c1_dq_oe;
    wire [15:0] bus0 = d0_oe ? d0_dq : 16'bz;
    wire [15:0] bus1 = d1_oe ? d1_dq : 16'bz;
    always @(bus0) c0_dq_in <= #(DLY) bus0;
    always @(bus1) c1_dq_in <= #(DLY) bus1;
    psx_cram_model #(.CHIP(0)) chip0 (.clk(d0_clk), .host_oe(d0_oe), .a(d0_a), .dq(bus0),
        .ce0_n(d0_ce0), .ce1_n(d0_ce1), .adv_n(d0_adv), .oe_n(d0_oen), .we_n(d0_wen), .ub_n(d0_ub), .lb_n(d0_lb), .cre(d0_cre));
    psx_cram_model #(.CHIP(1)) chip1 (.clk(d1_clk), .host_oe(d1_oe), .a(d1_a), .dq(bus1),
        .ce0_n(d1_ce0), .ce1_n(d1_ce1), .adv_n(d1_adv), .oe_n(d1_oen), .we_n(d1_wen), .ub_n(d1_ub), .lb_n(d1_lb), .cre(d1_cre));

    // reference blank card
    reg [13:0] blank_index = 14'h3FFF;
    wire [63:0] blank_word64;
    psx_memcard_blank blank_ref(.word_index(blank_index), .word(blank_word64));

    integer errors = 0;
    task fail(input [8*64-1:0] msg); begin
        errors = errors + 1;
        if (errors < 12) $display("ERROR t=%0t %0s", $time, msg);
    end endtask

    // card word i in the chips (DDR word 0x20000 + i/2, beat i[0]; cram0 low half)
    function [31:0] chip_word(input integer i);
        integer k;
        begin
            k = 2 * vp_index(22'h020000 + i / 2) + (i % 2);
            chip_word = {chip1.mem[k], chip0.mem[k]};
        end
    endfunction

    // ---------------- measurements ----------------
    realtime t_last_wr = 0, t_busy_fall = 0, t_fmt_rise = 0, t_fmt_fall = 0, t_acc = 0, t_last_done = 0;
    real last_aux_ns = -1;
    integer rd_lat_max = 0, rd_lat_cnt = 0, rd_count = 0, busy_falls = 0, idle_aux = 0, card_pulses = 0;
    integer aux_max_cyc = 0, aux_cyc = 0;
    reg rd_pending = 0, prev_busy = 1, prev_fmt = 0, watch_idle = 0, in_acc = 0, acc_last = 0;
    always @(posedge clk_74a) begin
        if (slot.rd_strobe && !slot.rst_74 && slot.req_accept) begin rd_pending <= 1; rd_lat_cnt <= 1; end
        else if (rd_pending) begin
            rd_lat_cnt <= rd_lat_cnt + 1;
            if (slot.rdv_edge) begin
                rd_pending <= 0; rd_count = rd_count + 1;
                if (rd_lat_cnt > rd_lat_max) rd_lat_max = rd_lat_cnt;
            end
        end
    end
    always @(posedge clk2x) begin
        prev_busy <= busy; prev_fmt <= slot.fmt_active;
        if (prev_busy && !busy) begin t_busy_fall = $realtime; busy_falls = busy_falls + 1; end
        if (!prev_fmt && slot.fmt_active) t_fmt_rise = $realtime;
        if (prev_fmt && !slot.fmt_active) t_fmt_fall = $realtime;
        if (watch_idle && aux_req) idle_aux = idle_aux + 1;
        if (card_write) card_pulses = card_pulses + 1;
        // aux handshake: request to aux_done
        if (aux_req && !in_acc && !aux_done) begin
            in_acc <= 1; t_acc = $realtime; aux_cyc = 0;
            acc_last <= aux_we && aux_addr == WORDS - 1;
        end else if (in_acc) aux_cyc = aux_cyc + 1;
        if (in_acc && aux_done) begin
            in_acc <= 0;
            if (aux_cyc + 1 > aux_max_cyc) aux_max_cyc = aux_cyc + 1;
            if (acc_last && busy) begin last_aux_ns = ($realtime - t_acc) / 1000.0; t_last_done = $realtime; end
        end
    end

    // ---------------- DDR master: guest card writes and GPU-style reads ----------------
    reg gw_req = 0, gw_ack = 0, gpu_on = 0;
    reg [14:0] gw_k = 0; reg [63:0] gw_d = 0; reg [7:0] gw_be = 0;
    integer gpu_bursts = 0, gpu_words = 0, guest_writes = 0;
    always begin : ddr_master
        integer n;
        @(posedge clk2x);
        if (gw_req && !gw_ack) begin
            ddr_we <= 1; ddr_addr <= 25'h020000 + gw_k; ddr_din <= gw_d; ddr_be <= gw_be;
            @(posedge clk2x); while (ddr_busy) @(posedge clk2x);
            ddr_we <= 0; gw_ack <= 1; guest_writes = guest_writes + 1;
        end else if (gpu_on && gpu_burst > 0 && gpu_wr && gpu_bursts % 2) begin
            // VRAM write stream (outside the card window, no card_write)
            // (one word per request, consecutive addresses, as the GPU writes)
            ddr_we <= 1; ddr_burstcnt <= 8'd1; ddr_addr <= $urandom % (22'h020000 - 256);
            ddr_din <= {$urandom, $urandom}; ddr_be <= 8'hFF; n = 0;
            while (n < gpu_burst) begin
                @(posedge clk2x);
                if (!ddr_busy) begin n = n + 1; ddr_din <= {$urandom, $urandom}; ddr_addr <= ddr_addr + 1; end
            end
            ddr_we <= 0;
            gpu_bursts = gpu_bursts + 1; gpu_words = gpu_words + gpu_burst;
            n = 0; while (n < gpu_gap && !gw_req) begin @(posedge clk2x); n = n + 1; end
        end else if (gpu_on && gpu_burst > 0) begin
            ddr_rd <= 1; ddr_burstcnt <= gpu_burst; ddr_addr <= $urandom % (22'h020000 - 256);
            @(posedge clk2x); while (ddr_busy) @(posedge clk2x);
            ddr_rd <= 0; n = 0;
            while (n < gpu_burst) begin @(posedge clk2x); if (ddr_dout_ready) n = n + 1; end
            gpu_bursts = gpu_bursts + 1; gpu_words = gpu_words + gpu_burst;
            // idle gap; a pending guest write goes first (the GPU and the
            // CPU share the DDR port through the core's arbiter)
            n = 0; while (n < gpu_gap && !gw_req) begin @(posedge clk2x); n = n + 1; end
        end
    end
    reg [31:0] expect_mem [0:WORDS-1];
    integer rb_j = -1, late_guest = 0;
    task guest_write(input integer k);
        reg [63:0] d; reg [7:0] be; integer b;
        begin
            d = {$urandom, $urandom}; be = $urandom; if (be == 0) be = 8'hFF;
            for (b = 0; b < 8; b = b + 1) if (be[b]) begin
                if (b < 4) expect_mem[2 * k][8 * b +: 8] = d[8 * b +: 8];
                else expect_mem[2 * k + 1][8 * (b - 4) +: 8] = d[8 * b +: 8];
            end
            gw_k = k; gw_d = d; gw_be = be; gw_ack = 0; gw_req = 1;
            wait (gw_ack); gw_req = 0;
            // bench self-check: the readback must not have read these words yet
            if (rb_j >= 0 && rb_j + 2 > 2 * k) begin   // two reads of margin (slot prefetch)
                late_guest = late_guest + 1;
                if (late_guest == 1) $display("ERROR bench: guest write to card word %0d landed after read %0d", 2 * k, rb_j);
            end
        end
    endtask

    // ---------------- APF ----------------
    function [31:0] swap32(input [31:0] w); swap32 = {w[7:0], w[15:8], w[23:16], w[31:24]}; endfunction
    reg done_after = 0;
    task apf_write(input [31:0] addr, input [31:0] data); begin
        @(posedge clk_74a); bridge_addr <= addr;
        repeat (30) @(posedge clk_74a);
        bridge_wr_data <= data;
        @(posedge clk_74a); bridge_wr <= 1; t_last_wr = $realtime;
        @(posedge clk_74a); bridge_wr <= 0;
        if (done_after) load_done <= 1;
        repeat (wper - 33) @(posedge clk_74a);
    end endtask
    reg [31:0] rd_sampled;
    task apf_read(input [31:0] addr); begin
        @(posedge clk_74a); bridge_addr <= addr;
        repeat (RD_SAMPLE - 1) @(posedge clk_74a);
        @(posedge clk_74a); rd_sampled = bridge_rd_data;
        bridge_rd <= 1;
        @(posedge clk_74a); bridge_rd <= 0;
        repeat (rper - RD_SAMPLE - 2) @(posedge clk_74a);
    end endtask

    reg [31:0] file_word [0:WORDS-1];
    integer i, k, bad, first_bad, rb1_bad, rb2_bad, rb_first;
    task readback(output integer nbad, input integer with_guest);
        integer j;
        begin
            nbad = 0; rb_first = -1;
            for (j = 0; j <= WORDS; j = j + 1) begin
                // a guest write to words three or four reads ahead (it lands
                // before they are read)
                if (with_guest && j + ahead + 1 < WORDS && j % 61 == 7) begin
                    fork guest_write((j + ahead) / 2); join_none
                end
                rb_j = j;
                apf_read(32'h2000_0000 + 4 * (j < WORDS ? j : WORDS - 1));
                if (j > 0 && rd_sampled !== expect_mem[j - 1]) begin
                    if (rb_first < 0) rb_first = j - 1; nbad = nbad + 1;
                end
            end
            rb_j = -1;
            if (nbad) $display("ERROR readback: %0d words wrong, first %0d", nbad, rb_first);
        end
    endtask

    initial begin
        #1;
        void'($urandom(seed));
        for (i = 0; i < WORDS; i = i + 1) file_word[i] = image == "ff" ? 32'hFFFF_FFFF : $urandom;
        if (image == "real") file_word[0][15:0] = 16'h434D;
        for (i = 0; i < WORDS; i = i + 1) expect_mem[i] = file_word[i];
        if (image != "real") for (i = 0; i < 2048; i = i + 1) begin
            blank_index = i >> 1; #1;
            expect_mem[i] = i[0] ? blank_word64[63:32] : blank_word64[31:0];
        end
        repeat (20) @(posedge clk2x); reset <= 0;
        // the top acknowledges APF slot writes once memory is ready (parked)
        if (park_load) wait (vram.st == 3'd5); else wait (vram.st == 3'd2);
        for (i = 0; i < WORDS; i = i + 1) begin
            done_after = i == WORDS - 1;
            apf_write(32'h2000_0000 + 4 * i, file_word[i]);   // bridge_endian_little=1: card word as is
        end
        wait (!busy);
        @(posedge clk2x); #1;
        bad = 0; first_bad = -1;
        for (k = 0; k < WORDS; k = k + 1) if (chip_word(k) !== expect_mem[k]) begin
            if (first_bad < 0) first_bad = k; bad = bad + 1;
        end
        if (bad) begin
            $display("ERROR image in the chips when busy fell: %0d words differ, first %0d got %08x want %08x",
                bad, first_bad, chip_word(first_bad), expect_mem[first_bad]);
            errors = errors + 1;
        end
        if (overflow) fail("overflow during load");

        // core runs: unpark, guest card writes, idle check
        park <= 0;
        wait (vram.st == 3'd2);
        for (i = 0; i < guest_n; i = i + 1) guest_write(i < 2 ? (i == 0 ? 0 : WORDS / 2 - 1) : $urandom % (WORDS / 2));
        i = 0;
        while (card_pulses != guest_writes && i < 100000) begin @(posedge clk2x); i = i + 1; end
        $display("last guest write landed %0d clk2x after acceptance", i);
        if (card_pulses != guest_writes) begin
            $display("ERROR card_write pulses %0d, guest card writes %0d", card_pulses, guest_writes); errors = errors + 1;
        end
        watch_idle = 1; repeat (3000) @(posedge clk2x); watch_idle = 0;
        if (idle_aux) fail("aux_req while idle");

        // APF readback with the core running (synchronous mode, GPU traffic)
        gpu_on = 1;
        readback(rb1_bad, 1);
        gpu_on = 0;
        if (rb1_bad) errors = errors + 1;
        repeat (200) @(posedge clk2x);
        // APF quit: park, parked (asynchronous) readback
        park <= 1;
        wait (vram.st == 3'd5);
        readback(rb2_bad, 0);
        if (rb2_bad) errors = errors + 1;
        if (overflow) fail("overflow during readback");
        if (busy_falls != 1) fail("busy fell more than once");
        if (late_guest) errors = errors + 1;

        $display("RESULT {\"bench\":\"integration\",\"image\":\"%0s\",\"park_load\":%0d,\"gpu_burst\":%0d,\"gpu_gap\":%0d,\"gpu_wr\":%0d,\"ahead\":%0d,\"seed\":%0d,\"wper\":%0d,\"rper\":%0d,\"phase_ps\":%0d,\"last_write_to_busy_ns\":%0.1f,\"last_write_aux_latency_ns\":%0.1f,\"last_write_done_to_busy_ns\":%0.1f,\"format_ns\":%0.1f,\"formatted\":%0d,\"aux_latency_max_clk2x\":%0d,\"read_strobe_to_data_max_clk74\":%0d,\"read_deadline_clk74\":%0d,\"read_slack_clk74\":%0d,\"reads\":%0d,\"readback_sync_words_wrong\":%0d,\"readback_parked_words_wrong\":%0d,\"idle_aux_cycles\":%0d,\"guest_writes\":%0d,\"card_write_pulses\":%0d,\"gpu_bursts\":%0d,\"gpu_words\":%0d,\"late_guest_writes\":%0d,\"overflow\":%0d,\"errors\":%0d}",
            image, park_load, gpu_burst, gpu_gap, gpu_wr, ahead, seed0, wper, rper, phase_ps, (t_busy_fall - t_last_wr) / 1000.0, last_aux_ns,
            (t_busy_fall - t_last_done) / 1000.0,
            t_fmt_fall > t_fmt_rise ? (t_fmt_fall - t_fmt_rise) / 1000.0 : 0.0, t_fmt_fall > 0,
            aux_max_cyc, rd_lat_max, rper - 2, rper - 2 - rd_lat_max, rd_count, rb1_bad, rb2_bad,
            idle_aux, guest_writes, card_pulses, gpu_bursts, gpu_words, late_guest, overflow, errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
    initial begin #400_000_000_000; $display("ERROR timeout"); $display("FAIL"); $finish; end
endmodule
`default_nettype wire
