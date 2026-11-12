// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
// save-checksum (scripts/prepare_save_checksum.py, audit-059 #8): psx_memcard_slot's sticky overflow is set
// only by a dropped save-window write (a lost upload word). Old slot (psx_memcard_slot_old, before the
// step) and new slot side by side on the same inputs:
//   - every output but overflow equal on every clk_74a and clk2x edge (the step changes nothing else);
//   - new overflow against a reference built from the OLD slot's own signals: set when a save-window
//     write strobe arrives while the request register is taken, cleared as the old one clears (reset,
//     the overflow_clear crossing);
//   - random bridge traffic (card window and save window, reads and writes, outside the windows),
//     strobes 1-4 clk_74a apart so many arrive while a request waits, an aux port answering after
//     1-60 clk2x, overflow_clear pulses and a few resets. Each kind of dropped strobe must occur.
`timescale 1ps/1ps
`default_nettype none
module tb_slot_overflow;
    reg clk_74a = 0, clk2x = 0;
    always #6734 clk_74a = !clk_74a;        // 74.25 MHz
    always #7385 clk2x = !clk2x;            // 67.7 MHz
    reg [31:0] bridge_addr = 0, bridge_wr_data = 0;
    reg bridge_wr = 0, bridge_rd = 0, reset_2x = 1, overflow_clear = 0, load_done = 0, park_req = 0;
    reg [31:0] aux_dout = 0; reg aux_done = 0;

    wire [31:0] o_rd, n_rd, o_din, n_din; wire [20:0] o_addr, n_addr;
    wire o_ovf, n_ovf, o_busy, n_busy, o_req, n_req, o_we, n_we, o_park, n_park;
    psx_memcard_slot_old #(.SAVE_BASE(32'h3000_0000)) old (
        .clk_74a(clk_74a), .bridge_addr(bridge_addr), .bridge_wr(bridge_wr), .bridge_wr_data(bridge_wr_data),
        .bridge_rd(bridge_rd), .bridge_rd_data(o_rd), .overflow(o_ovf), .clk2x(clk2x), .reset_2x(reset_2x),
        .overflow_clear(overflow_clear), .busy(o_busy), .aux_req(o_req), .aux_we(o_we), .aux_addr(o_addr),
        .aux_din(o_din), .aux_dout(aux_dout), .aux_done(aux_done), .card_write(1'b0), .load_done(load_done),
        .park_req(park_req), .park(o_park));
    psx_memcard_slot #(.SAVE_BASE(32'h3000_0000)) dut (
        .clk_74a(clk_74a), .bridge_addr(bridge_addr), .bridge_wr(bridge_wr), .bridge_wr_data(bridge_wr_data),
        .bridge_rd(bridge_rd), .bridge_rd_data(n_rd), .overflow(n_ovf), .clk2x(clk2x), .reset_2x(reset_2x),
        .overflow_clear(overflow_clear), .busy(n_busy), .aux_req(n_req), .aux_we(n_we), .aux_addr(n_addr),
        .aux_din(n_din), .aux_dout(aux_dout), .aux_done(aux_done), .card_write(1'b0), .load_done(load_done),
        .park_req(park_req), .park(n_park));

    integer errors = 0, edges74 = 0, edges2x = 0;
    integer drop_card_wr = 0, drop_card_rd = 0, drop_save_rd = 0, drop_save_wr = 0, old_sets = 0, new_sets = 0;
    integer ref_sets = 0, clears = 0, set_while_set = 0, ignored = 0;
    task fail(input [8*80-1:0] msg); begin
        errors = errors + 1;
        if (errors < 12) $display("ERROR t=%0t %0s (old ovf %b new %b)", $time, msg, o_ovf, n_ovf);
    end endtask

    // ---------------- checks ----------------
    reg ref_ovf = 0;
    always @(posedge clk_74a) begin
        // (values before this edge)
        edges74 = edges74 + 1;
        if ({o_rd, o_park} !== {n_rd, n_park}) fail("a clk_74a output differs");
        if (n_ovf !== ref_ovf) fail("overflow differs from the reference");
        if ((old.wr_strobe || old.rd_strobe) && old.pending) begin
            if (old.in_save) begin if (old.wr_strobe) drop_save_wr = drop_save_wr + 1; else drop_save_rd = drop_save_rd + 1; end
            else begin if (old.wr_strobe) drop_card_wr = drop_card_wr + 1; else drop_card_rd = drop_card_rd + 1; end
        end
        if (!o_ovf && (old.wr_strobe || old.rd_strobe) && old.pending && !old.rst_74) old_sets = old_sets + 1;
        // a card drop or a read-out drop while the new flag is clear: it must stay clear
        if (!ref_ovf && (old.wr_strobe || old.rd_strobe) && old.pending && !(old.wr_strobe && old.in_save) && !old.rst_74) ignored = ignored + 1;
        // the reference, in the slot's order: reset; else the clear, then a drop sets (a drop wins)
        if (old.rst_74) ref_ovf = 0;
        else begin
            if (old.s74_clr[1] != old.clr_seen_74) begin if (ref_ovf) clears = clears + 1; ref_ovf = 0; end
            if (old.wr_strobe && old.in_save && old.pending) begin
                if (ref_ovf) set_while_set = set_while_set + 1; else ref_sets = ref_sets + 1;
                ref_ovf = 1;
            end
        end
    end
    always @(posedge clk2x) begin
        edges2x = edges2x + 1;
        if ({o_busy, o_req, o_we, o_addr, o_din} !== {n_busy, n_req, n_we, n_addr, n_din}) fail("a clk2x output differs");
    end
    reg n_ovf_d = 0;
    always @(posedge clk_74a) begin n_ovf_d <= n_ovf; if (n_ovf && !n_ovf_d) new_sets = new_sets + 1; end

    // ---------------- the aux port: answers 1-60 clk2x after a request ----------------
    integer wait2x = -1;
    always @(posedge clk2x) begin
        aux_done <= 0;
        if (o_req && !aux_done && wait2x < 0) wait2x = 1 + ($urandom % 60);
        else if (wait2x > 0) wait2x = wait2x - 1;
        if (wait2x == 0) begin aux_done <= 1; aux_dout <= $urandom; wait2x = -1; end
        overflow_clear <= ($urandom % 400) == 0;
        load_done <= ($urandom % 3000) == 0 ? !load_done : load_done;
    end

    // ---------------- the bridge ----------------
    integer n, gap, kind, resets = 0;
    initial begin
        if (!$value$plusargs("N=%d", n)) n = 60000;
        repeat (20) @(posedge clk2x);
        reset_2x <= 0;
        repeat (40) @(posedge clk_74a);
        while (n > 0) begin
            n = n - 1;
            gap = 1 + ($urandom % 4); if (($urandom % 50) == 0) gap = 40 + ($urandom % 200);
            repeat (gap) @(posedge clk_74a);
            kind = $urandom % 10;
            bridge_addr <= kind < 4 ? 32'h2000_0000 | ($urandom & 32'h0001_FFFC) :
                           kind < 9 ? 32'h3000_0000 | ($urandom & 32'h003F_FFFC) : 32'h1000_0000 | ($urandom & 32'hFFFC);
            bridge_wr_data <= $urandom;
            if ($urandom % 2) bridge_wr <= 1; else bridge_rd <= 1;
            park_req <= ($urandom % 500) == 0 ? !park_req : park_req;
            @(posedge clk_74a); bridge_wr <= 0; bridge_rd <= 0;
            if (($urandom % 5000) == 0) begin
                resets = resets + 1;
                @(posedge clk2x); reset_2x <= 1; repeat (3) @(posedge clk2x); reset_2x <= 0;
            end
        end
        repeat (200) @(posedge clk2x);
        if (drop_card_wr < 50 || drop_card_rd < 50 || drop_save_rd < 50 || drop_save_wr < 50) fail("too few drops of some kind");
        if (ref_sets < 20 || clears < 20) fail("too few reference sets or clears");
        if (ignored < 50) fail("too few card or read-out drops while the new flag was clear");
        $display("SLOTOVF edges74=%0d edges2x=%0d drops card_wr=%0d card_rd=%0d save_rd=%0d save_wr=%0d old_sets=%0d new_sets=%0d ref_sets=%0d ignored_drops=%0d clears=%0d resets=%0d",
                 edges74, edges2x, drop_card_wr, drop_card_rd, drop_save_rd, drop_save_wr, old_sets, new_sets, ref_sets, ignored, clears, resets);
        if (errors == 0) $display("PASS slot overflow"); else $display("FAIL slot overflow: %0d errors", errors);
        $finish;
    end
endmodule
`default_nettype wire
