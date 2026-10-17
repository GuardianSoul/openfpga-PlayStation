// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Bench for rtl/memory/psx_vram_psram.sv: the controller drives two
// psx_cram_model chips through per-group transport delays. A MiSTer-style
// DDR master (GPU line reads with gpu.vhd's burst rounding/split/wrap, GPU
// pixel writes and rect runs with pixel byte-enable pairs, render24/interlace
// write pairs, memcard.vhd 128-word block reads and single BE=FF writes,
// arbitrary byte enables, die 1) runs against a golden memory, with the aux
// port on its own part of card 1, resets and parks at random times. A
// handoff phase checks the aux <-> DDR byte mapping both ways.
// Prints "VP_HIST {json}" / "VP_MEASURE {json}" and "VRAM_PSRAM PASS".
`timescale 1ns/1ps
`default_nettype none
module tb_psx_vram_psram;
    parameter integer SEED = 1;
    parameter integer N_OPS = 2500;
    parameter integer MEASURE = 0;
    parameter integer RESETS = 3;
    parameter integer PARKS = 3;
    parameter integer LATENCY = 3;
    parameter integer ROW_LOG2 = 6;
    parameter integer AUX = 1;
    parameter integer AUX_MAX = 60;          // aux request -> done bound while the core runs (clk2x)               // aux traffic during the random phase
    parameter real CLK_DLY = 1.0, CTL_DLY = 1.0, DQ_DLY = 1.0, RET_DLY = 1.0;
    parameter real T_ACLK = 9.0, T_KOH = 2.0;
    localparam real PERIOD = 14.763;          // clk2x 67.7376 MHz

    `include "psx_vram_psram_windows.svh"

    reg clk = 0;
    always #(PERIOD/2) clk = !clk;
    function automatic integer now_cyc; now_cyc = $rtoi($realtime / PERIOD); endfunction

    reg reset = 1, park = 0;
    reg [7:0] ddr_burstcnt = 0; reg [24:0] ddr_addr = 0; reg ddr_rd = 0, ddr_we = 0;
    reg [63:0] ddr_din = 0; reg [7:0] ddr_be = 0;
    wire ddr_busy, ddr_dout_ready; wire [63:0] ddr_dout;
    reg aux_req = 0, aux_we = 0; reg [20:0] aux_addr = 0; reg [31:0] aux_din = 0;
    wire [31:0] aux_dout; wire aux_done, card_write;

    wire [21:16] c0_a, c1_a; wire [15:0] c0_dq_out, c1_dq_out; wire c0_dq_oe, c1_dq_oe;
    wire c0_clk, c1_clk, c0_adv_n, c1_adv_n, c0_cre, c1_cre, c0_ce0_n, c1_ce0_n, c0_ce1_n, c1_ce1_n;
    wire c0_oe_n, c1_oe_n, c0_we_n, c1_we_n, c0_ub_n, c1_ub_n, c0_lb_n, c1_lb_n;
    reg  [15:0] c0_dq_in = 16'hzzzz, c1_dq_in = 16'hzzzz;

    psx_vram_psram #(.LATENCY(LATENCY), .ROW_LOG2(ROW_LOG2)) dut (
        .clk(clk), .reset(reset), .park(park),
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

    // ---- board: transport delays per group (clock / control+address / data out / data back)
    reg [5:0] d0_a = 0, d1_a = 0; reg [15:0] d0_dq = 0, d1_dq = 0; reg d0_oe = 0, d1_oe = 0;
    reg d0_clk = 0, d1_clk = 0, d0_adv = 1, d1_adv = 1, d0_cre = 0, d1_cre = 0, d0_ce0 = 1, d1_ce0 = 1;
    reg d0_ce1 = 1, d1_ce1 = 1, d0_oen = 1, d1_oen = 1, d0_wen = 1, d1_wen = 1, d0_ub = 1, d1_ub = 1, d0_lb = 1, d1_lb = 1;
    always @(c0_clk) d0_clk <= #(CLK_DLY) c0_clk;        always @(c1_clk) d1_clk <= #(CLK_DLY) c1_clk;
    always @(c0_a) d0_a <= #(CTL_DLY) c0_a;              always @(c1_a) d1_a <= #(CTL_DLY) c1_a;
    always @(c0_adv_n) d0_adv <= #(CTL_DLY) c0_adv_n;    always @(c1_adv_n) d1_adv <= #(CTL_DLY) c1_adv_n;
    always @(c0_cre) d0_cre <= #(CTL_DLY) c0_cre;        always @(c1_cre) d1_cre <= #(CTL_DLY) c1_cre;
    always @(c0_ce0_n) d0_ce0 <= #(CTL_DLY) c0_ce0_n;    always @(c1_ce0_n) d1_ce0 <= #(CTL_DLY) c1_ce0_n;
    always @(c0_ce1_n) d0_ce1 <= #(CTL_DLY) c0_ce1_n;    always @(c1_ce1_n) d1_ce1 <= #(CTL_DLY) c1_ce1_n;
    always @(c0_oe_n) d0_oen <= #(CTL_DLY) c0_oe_n;      always @(c1_oe_n) d1_oen <= #(CTL_DLY) c1_oe_n;
    always @(c0_we_n) d0_wen <= #(CTL_DLY) c0_we_n;      always @(c1_we_n) d1_wen <= #(CTL_DLY) c1_we_n;
    always @(c0_ub_n) d0_ub <= #(CTL_DLY) c0_ub_n;       always @(c1_ub_n) d1_ub <= #(CTL_DLY) c1_ub_n;
    always @(c0_lb_n) d0_lb <= #(CTL_DLY) c0_lb_n;       always @(c1_lb_n) d1_lb <= #(CTL_DLY) c1_lb_n;
    always @(c0_dq_out) d0_dq <= #(DQ_DLY) c0_dq_out;    always @(c1_dq_out) d1_dq <= #(DQ_DLY) c1_dq_out;
    always @(c0_dq_oe) d0_oe <= #(DQ_DLY) c0_dq_oe;      always @(c1_dq_oe) d1_oe <= #(DQ_DLY) c1_dq_oe;
    wire [15:0] bus0 = d0_oe ? d0_dq : 16'bz;
    wire [15:0] bus1 = d1_oe ? d1_dq : 16'bz;
    always @(bus0) c0_dq_in <= #(RET_DLY) bus0;
    always @(bus1) c1_dq_in <= #(RET_DLY) bus1;

    psx_cram_model #(.CHIP(0), .T_ACLK(T_ACLK), .T_KOH(T_KOH)) chip0 (.clk(d0_clk), .host_oe(d0_oe), .a(d0_a), .dq(bus0),
        .ce0_n(d0_ce0), .ce1_n(d0_ce1), .adv_n(d0_adv), .oe_n(d0_oen), .we_n(d0_wen), .ub_n(d0_ub), .lb_n(d0_lb), .cre(d0_cre));
    psx_cram_model #(.CHIP(1), .T_ACLK(T_ACLK), .T_KOH(T_KOH)) chip1 (.clk(d1_clk), .host_oe(d1_oe), .a(d1_a), .dq(bus1),
        .ce0_n(d1_ce0), .ce1_n(d1_ce1), .adv_n(d1_adv), .oe_n(d1_oen), .we_n(d1_wen), .ub_n(d1_ub), .lb_n(d1_lb), .cre(d1_cre));

    // ---- golden memory
    reg [63:0] g [0:VP_WORDS-1];
    task automatic gseed(input [21:0] w);
        integer i; i = vp_index(w);
        g[i] = {vp_seed(1, w, 1), vp_seed(0, w, 1), vp_seed(1, w, 0), vp_seed(0, w, 0)};
    endtask
    initial begin : golden_init
        reg [21:0] w;
        for (w = 22'h000000; w < 22'h024000; w = w + 1) gseed(w);
        for (w = 22'h040000; w < 22'h044000; w = w + 1) gseed(w);
        for (w = 22'h060000; w < 22'h070000; w = w + 1) gseed(w);
        for (w = 22'h080000; w < 22'h0A0000; w = w + 1) gseed(w);
        for (w = 22'h0E0000; w < 22'h100000; w = w + 1) gseed(w);
        for (w = 22'h200000; w < 22'h204000; w = w + 1) gseed(w);
        for (w = 22'h3FC000; w != 22'h000000; w = w + 1) gseed(w);
    end
    // one aux write cut by reset may or may not have landed
    reg amb_on = 0; reg [21:0] amb_w = 0; reg [63:0] amb_new = 0; reg [14:0] amb_i = 0;

    task automatic fail(input [8*80-1:0] what);
        begin $display("VRAM_PSRAM FAIL %0s at %0t", what, $realtime); $fatal(1); end
    endtask
    function automatic [63:0] apply_be(input [63:0] old, input [63:0] d, input [7:0] be);
        integer i;
        apply_be = old;
        for (i = 0; i < 8; i = i + 1) if (be[i]) apply_be[8*i +: 8] = d[8*i +: 8];
    endfunction
    function automatic [63:0] expect_word(input [21:0] w, input [63:0] got);
        integer i;
        i = vp_index(w);
        if (i < 0) begin $display("bench address %h outside windows", w); $fatal(1); end
        if (amb_on && amb_w == w && got === amb_new) begin g[i] = got; amb_on = 0; end
        else if (amb_on && amb_w == w && got === g[i]) amb_on = 0;
        expect_word = g[i];
    endfunction

    // ---- statistics (cycles of clk2x)
    integer h_rd_first [0:1023], h_rd_done [0:1023], h_wr_acc [0:1023], h_aux_rd [0:1023], h_aux_wr [0:1023];
    integer n_reads = 0, n_read_words = 0, n_writes = 0, n_card_writes = 0, n_aux = 0, card_pulses = 0;
    integer n_resets = 0, n_resets_ce_low = 0, n_resets_in_write = 0, n_parks = 0, n_aborted = 0;
    initial for (integer i = 0; i < 1024; i = i + 1) begin
        h_rd_first[i] = 0; h_rd_done[i] = 0; h_wr_acc[i] = 0; h_aux_rd[i] = 0; h_aux_wr[i] = 0;
    end
    function automatic integer clampi(input integer v); clampi = v > 1023 ? 1023 : v < 0 ? 0 : v; endfunction
    always @(posedge clk) if (card_write) card_pulses = card_pulses + 1;

    // ---- DDR master: one process, every edge goes through tick
    reg rd_on = 0, rd_first = 0, rst_seen = 0;
    reg [21:0] wr_ring [0:7];
    integer wr_ring_n = 0;
    // after a reset: every write accepted just before it must be whole
    task automatic check_recent_writes;
        integer k, ok;
        begin
            rst_seen = 0;
            for (k = 0; k < 8 && k < wr_ring_n; k = k + 1) ddr_read(wr_ring[k], 1, ok);
        end
    endtask
    reg [21:0] rd_w = 0;
    integer rd_left = 0, rd_t0 = 0, rd_tfirst = 0, rd_tlast = 0, last_wr_acc = 0;
    task automatic tick;
        reg [63:0] e;
        begin
            @(posedge clk);
            if (ddr_dout_ready) begin
                if (!rd_on) fail("ddr_dout_ready without an outstanding read");
                e = expect_word(rd_w, ddr_dout);
                if (ddr_dout !== e) begin
                    $display("read word %h got %h expected %h", rd_w, ddr_dout, e);
                    fail("DDR read data mismatch");
                end
                if (!rd_first) begin rd_first = 1; rd_tfirst = now_cyc(); end
                rd_w = rd_w + 1; rd_left = rd_left - 1; n_read_words = n_read_words + 1;
                if (rd_left == 0) begin rd_on = 0; rd_tlast = now_cyc(); end
            end
        end
    endtask

    task automatic ddr_read(input [21:0] w, input integer n, output integer ok);
        integer t0, k;
        begin
            ok = 0;
            for (k = 0; k < n; k = k + 1) if (vp_index(w + k) < 0) fail("bench burst leaves the windows");
            ddr_addr <= {3'b000, w}; ddr_burstcnt <= n[7:0]; ddr_rd <= 1'b1; t0 = now_cyc();
            begin : wait_accept
                reg quit; quit = 0;
                while (!quit) begin
                    tick();
                    if (!ddr_busy) begin ok = 1; quit = 1; end
                    else if (reset) quit = 1;
                end
            end
            ddr_rd <= 1'b0;
            if (ok) begin
                rd_on = 1; rd_first = 0; rd_w = w; rd_left = n == 0 ? 256 : n; rd_t0 = t0;
                while (rd_on && !reset) tick();
                if (rd_on) begin rd_on = 0; ok = 0; n_aborted = n_aborted + 1; end
                else begin
                    n_reads = n_reads + 1;
                    h_rd_first[clampi(rd_tfirst - t0)] += 1;
                    h_rd_done[clampi(rd_tlast - t0)] += 1;
                end
            end
        end
    endtask

    task automatic ddr_write(input [21:0] w, input [63:0] d, input [7:0] be, output integer ok);
        integer t0, i;
        begin
            ok = 0; i = vp_index(w);
            if (i < 0) fail("bench write outside the windows");
            ddr_addr <= {3'b000, w}; ddr_din <= d; ddr_be <= be; ddr_we <= 1'b1; ddr_burstcnt <= 8'd1; t0 = now_cyc();
            begin : wait_accept
                reg quit; quit = 0;
                while (!quit) begin
                    tick();
                    if (!ddr_busy) begin ok = 1; quit = 1; end
                    else if (reset) quit = 1;
                end
            end
            ddr_we <= 1'b0;
            if (ok) begin
                g[i] = apply_be(g[i], d, be);
                wr_ring[wr_ring_n % 8] = w; wr_ring_n = wr_ring_n + 1;
                if (amb_on && amb_w == w) fail("bench: DDR write to an ambiguous aux word");
                n_writes = n_writes + 1; last_wr_acc = now_cyc();
                h_wr_acc[clampi(now_cyc() - t0)] += 1;
                if (w >= 22'h020000 && w < 22'h024000) n_card_writes = n_card_writes + 1;
            end else n_aborted = n_aborted + 1;
        end
    endtask

    task automatic idle(input integer n);
        repeat (n) tick();
    endtask

    // gpu.vhd READVRAM: size rounding, 128-word split at ADDR(10), vram2vram wrap
    task automatic gpu_line_read(input [21:0] base, input integer x, input integer y, input integer size, input integer wrap_on);
        integer r, first, nxt, wrap, ok;
        reg [21:0] w;
        begin
            r = size;
            if (size % 4) r = (size / 4 + 1) * 4;
            if ((x % 4) != 0 && (x % 4) + size > 4) r = r + 4;
            if (r > 1024) r = 1024;
            w = base + {y[8:0], x[9:2]};
            if (r > 512) begin first = 128; nxt = (r - 512) / 4; end
            else begin first = (r / 4) % 256; nxt = 0; end
            wrap = (wrap_on && x + r > 1024) ? (x + r - 1024) / 4 : 0;
            ddr_read(w, first, ok);
            if (ok && nxt > 0) ddr_read(w | 22'h80, nxt, ok);
            if (ok && wrap > 0) begin
                w = base + {y[8:0], 8'd0};
                if (wrap > 128) begin ddr_read(w, 128, ok); if (ok) ddr_read(w | 22'h80, wrap - 128, ok); end
                else ddr_read(w, wrap, ok);
            end
        end
    endtask

    function automatic [7:0] pixel_be(input [3:0] m); pixel_be = {{2{m[3]}}, {2{m[2]}}, {2{m[1]}}, {2{m[0]}}}; endfunction

    // ---- aux master (card 1 words 0x4000..0x7fff = DDR words 0x22000..0x23fff)
    reg aux_on = 0, aux_idle = 1, aux_last_valid = 0;
    integer aux_max_running = 0;
    reg [31:0] aux_last = 0;
    integer aux_seed = 0;
    task automatic aux_op(input we, input [14:0] i, input [31:0] d, output integer ok);
        integer t0, gi;
        reg [63:0] e;
        begin
            ok = 0;
            if (aux_last_valid && aux_dout !== aux_last) fail("aux_dout not held between accesses");
            aux_addr <= {6'd0, i}; aux_we <= we; aux_din <= d; aux_req <= 1'b1; t0 = now_cyc();
            gi = vp_index(22'h020000 + i[14:1]);
            begin : wait_done
                reg quit, upset; quit = 0; upset = 0;
                while (!quit) begin
                    @(posedge clk);
                    // only accesses that saw the controller running all along are bounded
                    if (reset || park || !(dut.st == 3'd2 || dut.st == 3'd3 || dut.st == 3'd4)) upset = 1;
                    if (aux_done) begin ok = 1; quit = 1; end
                    else if (reset) quit = 1;
                end
                if (ok && !upset) begin
                    if (now_cyc() - t0 > aux_max_running) aux_max_running = now_cyc() - t0;
                    if (now_cyc() - t0 > AUX_MAX) begin
                        $display("aux access took %0d cycles", now_cyc() - t0);
                        fail("aux latency bound exceeded while the core runs");
                    end
                end
            end
            aux_req <= 1'b0;
            if (ok) begin
                n_aux = n_aux + 1;
                if (we) begin
                    e = g[gi]; if (i[0]) e[63:32] = d; else e[31:0] = d; g[gi] = e;
                    h_aux_wr[clampi(now_cyc() - t0)] += 1; aux_last_valid = 0;
                end else begin
                    if (amb_on && amb_w == 22'h020000 + i[14:1] && i == amb_i) begin
                        // the read half is the one the cut write targeted: either value is right
                        if (aux_dout === (i[0] ? amb_new[63:32] : amb_new[31:0])) g[gi] = amb_new;
                        amb_on = 0;
                    end
                    e = g[gi];
                    if (aux_dout !== (i[0] ? e[63:32] : e[31:0])) begin
                        $display("aux word %h got %h expected %h", i, aux_dout, i[0] ? e[63:32] : e[31:0]);
                        fail("aux read mismatch");
                    end
                    h_aux_rd[clampi(now_cyc() - t0)] += 1; aux_last = aux_dout; aux_last_valid = 1;
                end
            end else begin
                aux_last_valid = 0;
                if (we) begin
                    if (amb_on) fail("bench: second ambiguous aux write");
                    e = g[gi]; if (i[0]) e[63:32] = d; else e[31:0] = d;
                    amb_on = 1; amb_w = 22'h020000 + i[14:1]; amb_new = e; amb_i = i;
                end
            end
        end
    endtask
    initial begin : aux_master
        integer ok, r;
        reg [14:0] i;
        aux_seed = SEED * 7919 + 13;
        forever begin
            if (aux_on) begin
                aux_idle = 0;
                if (amb_on) i = amb_i;                   // revisit it
                else i = 15'h4000 | 15'($urandom(aux_seed) & 15'h3fff);
                r = $urandom(aux_seed) % 100;
                aux_op(r < 50 && !amb_on, i, $urandom(aux_seed), ok);   // resolve an ambiguous word by reading it
                r = $urandom(aux_seed) % 4;
                if (r == 0) repeat ($urandom(aux_seed) % 200) @(posedge clk);
                if (reset) wait (!reset);
            end else begin aux_idle = 1; @(posedge clk); end
        end
    end

    // ---- resets and parks at random times
    reg chaos_on = 0;
    function automatic [15:0] bcr_of(input integer c, input integer d);
        bcr_of = c == 0 ? chip0.bcr[d] : chip1.bcr[d];
    endfunction
    function automatic all_bcr(input [15:0] v);
        all_bcr = chip0.bcr[0] == v && chip0.bcr[1] == v && chip1.bcr[0] == v && chip1.bcr[1] == v;
    endfunction
    localparam [15:0] BCR_SYNC = {1'b0, 1'b1, 3'(LATENCY), 1'b1, 1'b0, 1'b1, 2'b00, 2'b01, 1'b1, 3'b111};
    task automatic do_park;
        integer t, e0, hold;
        begin
            @(posedge clk); park <= 1'b1; t = now_cyc();
            while (!(all_bcr(16'h9D1F) && (dut.st == 3'd5 || dut.st == 3'd6))) begin
                @(posedge clk);
                if (now_cyc() - t > 16000) fail("park did not restore the BCRs in time");  // may follow a reset: power-up wait first
            end
            $display("park: BCRs 9D1F on both dies of both chips after %0d cycles", now_cyc() - t);
            e0 = chip0.clk_edges + chip1.clk_edges;
            hold = 300 + $urandom(aux_seed) % 3000;
            repeat (hold) begin
                @(posedge clk);
                if (!ddr_busy) fail("ddr_busy low while parked");
                if (ddr_dout_ready) fail("DDR data while parked");
                if (!d0_ce1 || !d1_ce1) fail("die 1 selected while parked");
            end
            if (chip0.clk_edges + chip1.clk_edges != e0) fail("chip clock ran while parked");
            if (!all_bcr(16'h9D1F)) fail("BCR changed while parked");
            park <= 1'b0; t = now_cyc();
            while (!all_bcr(BCR_SYNC)) begin
                @(posedge clk);
                if (now_cyc() - t > 200) fail("unpark did not reconfigure");
            end
            n_parks = n_parks + 1;
        end
    endtask
    task automatic do_reset;
        integer n;
        begin
            @(posedge clk);
            // land inside a segment: alternately a write and a read on the pins
            n = 0;
            while (n < 3000 && !(chip0.burst && chip0.wr == (n_resets % 2 == 0))) begin @(posedge clk); n = n + 1; end
            repeat ($urandom(aux_seed) % 4) @(posedge clk);
            if (!d0_ce0 || !d0_ce1) n_resets_ce_low = n_resets_ce_low + 1;
            if (chip0.burst && chip0.wr) n_resets_in_write = n_resets_in_write + 1;
            reset <= 1'b1; n = 1 + $urandom(aux_seed) % 40; rst_seen = 1;
            repeat (n) @(posedge clk);
            reset <= 1'b0; n_resets = n_resets + 1;
        end
    endtask
    initial begin : chaos
        integer k, gap, rs, ps;
        rs = RESETS; ps = PARKS; k = 0;
        wait (chaos_on);
        while (chaos_on && (rs > 0 || ps > 0)) begin
            gap = 4000 + $urandom(aux_seed) % 20000;
            repeat (gap) @(posedge clk);
            if (!chaos_on) disable chaos;
            if (rs > 0 && (k % 2 == 0 || ps == 0)) begin do_reset(); rs = rs - 1; end
            else if (ps > 0) begin do_park(); ps = ps - 1; end
            k = k + 1;
        end
    end

    // ---- histograms as JSON
    task automatic print_hist(input [8*40-1:0] name, input integer which);
        integer i, first, v;
        begin
            $write("\"%0s\":{", name); first = 1;
            for (i = 0; i < 1024; i = i + 1) begin
                case (which)
                0: v = h_rd_first[i]; 1: v = h_rd_done[i]; 2: v = h_wr_acc[i];
                3: v = h_aux_rd[i]; default: v = h_aux_wr[i];
                endcase
                if (v) begin $write("%0s\"%0d\":%0d", first ? "" : ",", i, v); first = 0; end
            end
            $write("}");
        end
    endtask
    task automatic clear_hist;
        for (integer i = 0; i < 1024; i = i + 1) begin
            h_rd_first[i] = 0; h_rd_done[i] = 0; h_wr_acc[i] = 0; h_aux_rd[i] = 0; h_aux_wr[i] = 0;
        end
    endtask

    // ---- directed latency / bandwidth measurement
    task automatic measure;
        integer ok, i, t0, t1, n, seed;
        reg [21:0] w;
        begin
            seed = SEED;
            ddr_read(22'h0, 1, ok);                  // past the power-up wait and configuration
            idle(10);
            $write("VP_MEASURE {");
            // single words, request presented with every phase
            clear_hist();
            for (i = 0; i < 64; i = i + 1) begin ddr_read(22'h001000 + 37 * i, 1, ok); idle(i % 3); end
            print_hist("read1_first", 0);
            // 128-word reads: aligned to a row, half a row in, one word in
            foreach_offset(0, "read128_row_aligned");
            foreach_offset(32, "read128_offset32");
            foreach_offset(1, "read128_offset1");
            // memcard block read
            clear_hist();
            for (i = 0; i < 8; i = i + 1) ddr_read(22'h040000 + 128 * i, 128, ok);
            $write(","); print_hist("card_read128_done", 1);
            // full 1024-pixel GPU line (two bursts back to back)
            t0 = now_cyc();
            for (i = 0; i < 4; i = i + 1) gpu_line_read(22'h0, 0, 100 + i, 1024, 0);
            $write(",\"gpu_line1024_cycles_per_line\":%0.2f", (now_cyc() - t0) / 4.0);
            // sequential writes (rect fill): next presented on the accepting edge
            t0 = now_cyc();
            for (i = 0; i < 256; i = i + 1) ddr_write(22'h002000 + i, {2{$urandom(seed)}}, 8'hFF, ok);
            t1 = last_wr_acc;
            idle(40);
            $write(",\"write_sequential_cycles_per_word\":%0.3f", (t1 - t0) / 256.0);
            t0 = now_cyc();
            for (i = 0; i < 128; i = i + 1) ddr_write(22'h003000 + 2 * i, {2{$urandom(seed)}}, 8'hFF, ok);
            t1 = last_wr_acc;
            idle(40);
            $write(",\"write_scattered_cycles_per_word\":%0.3f", (t1 - t0) / 128.0);
            // render24 / interlace pair: word, then the same word in the 0x80000 window
            t0 = now_cyc();
            for (i = 0; i < 64; i = i + 1) begin
                ddr_write(22'h004000 + i, {2{$urandom(seed)}}, 8'h0F, ok);
                ddr_write(22'h084000 + i, {2{$urandom(seed)}}, 8'h0F, ok);
            end
            t1 = last_wr_acc; idle(40);
            $write(",\"write_pair_cycles_per_pair\":%0.3f", (t1 - t0) / 64.0);
            clear_hist();
            for (i = 0; i < 64; i = i + 1) begin ddr_write(22'h005000 + 3 * i, 64'h0, 8'h03, ok); idle(20 + i % 2); end
            $write(","); print_hist("write_isolated_accept", 2);
            // read right after a write (posted write in front)
            clear_hist();
            for (i = 0; i < 32; i = i + 1) begin
                ddr_write(22'h006000 + 5 * i, 64'h0, 8'hFF, ok); ddr_read(22'h007000 + 9 * i, 1, ok); idle(i % 2);
            end
            $write(","); print_hist("read1_after_write_first", 0);
            // aux
            clear_hist();
            aux_last_valid = 0;
            for (i = 0; i < 32; i = i + 1) begin aux_op(i % 2, 15'h4000 + i, $urandom(seed), ok); repeat (i % 3 + 1) @(posedge clk); end
            $write(","); print_hist("aux_read_done", 3); $write(","); print_hist("aux_write_done", 4);
            $display("}");
        end
    endtask
    task automatic foreach_offset(input integer off, input [8*24-1:0] name);
        integer i, ok, a0, t0, sum_first, sum_rate;
        real rate;
        begin
            clear_hist(); rate = 0.0;
            for (i = 0; i < 8; i = i + 1) begin
                ddr_read(22'h010000 + 256 * i + off, 128, ok);
                rate = rate + (rd_tlast - rd_tfirst) / 127.0;
            end
            $write(",");
            print_hist({name, "_first"}, 0); $write(","); print_hist({name, "_done"}, 1);
            $write(",\"%0s_cycles_per_word\":%0.3f", name, rate / 8.0);
        end
    endtask

    // ---- main
    initial begin : main
        integer i, ok, op, n, x, y, sz, r, seed, base, t;
        reg [21:0] w;
        reg [63:0] d;
        reg [7:0] be;
        seed = SEED;
        repeat (20) @(posedge clk);
        reset <= 1'b0;
        if (MEASURE) begin
            measure();
            $display("VRAM_PSRAM PASS measure");
            $finish;
        end
        aux_on = AUX != 0; chaos_on = 1;
        for (op = 0; op < N_OPS; op = op + 1) begin
            if (rst_seen && !reset) check_recent_writes();
            r = $urandom(seed) % 100;
            if (r < 28) begin                         // GPU line / span read
                x = $urandom(seed) % 1024; y = $urandom(seed) % 511;
                sz = ($urandom(seed) % 4 == 0) ? 1 + $urandom(seed) % 1024 : 1 + $urandom(seed) % 64;
                gpu_line_read(($urandom(seed) % 4 == 0) ? 22'h080000 : 22'h0, x, y, sz, $urandom(seed) % 2);
            end else if (r < 33) begin                // memcard block read
                w = (($urandom(seed) % 2) ? 22'h040000 : 22'h020000) + 128 * ($urandom(seed) % 64);
                ddr_read(w, 128, ok);
            end else if (r < 36) begin                // memcard writes (BE=FF)
                w = (($urandom(seed) % 2) ? 22'h040000 : 22'h020000) + $urandom(seed) % 22'h1F00;
                n = 1 + $urandom(seed) % 16;
                for (i = 0; i < n; i = i + 1) ddr_write(w + i, {$urandom(seed), $urandom(seed)}, 8'hFF, ok);
            end else if (r < 66) begin                // GPU pixel writes / rect runs
                w = (($urandom(seed) % 4 == 0) ? 22'h080000 : 22'h0) + $urandom(seed) % 22'h1FF00;
                n = ($urandom(seed) % 3 == 0) ? 1 + $urandom(seed) % 80 : 1;
                for (i = 0; i < n; i = i + 1) begin
                    be = ($urandom(seed) % 5 == 0) ? 8'($urandom(seed)) : pixel_be(4'($urandom(seed)));
                    ddr_write(w + i, {$urandom(seed), $urandom(seed)}, be, ok);
                end
            end else if (r < 74) begin                // render24 / interlace second write
                w = $urandom(seed) % 22'h20000;
                be = pixel_be(4'($urandom(seed)));
                ddr_write(w, {$urandom(seed), $urandom(seed)}, be, ok);
                base = 22'h080000 + (($urandom(seed) % 2) ? 22'h060000 : 22'h0);
                ddr_write(base + w, {$urandom(seed), $urandom(seed)}, be, ok);
            end else if (r < 86) begin                // small reads anywhere (SPU, frames, die 1)
                case ($urandom(seed) % 5)
                0: w = 22'h060000 + $urandom(seed) % 22'hFF00;
                1: w = 22'h0E0000 + $urandom(seed) % 22'h1FF00;
                2: w = 22'h200000 + $urandom(seed) % 22'h3F00;
                3: w = 22'h3FC000 + $urandom(seed) % 22'h3F00;
                default: w = $urandom(seed) % 22'h1FF00;
                endcase
                ddr_read(w, 1 + $urandom(seed) % (($urandom(seed) % 4 == 0) ? 200 : 16), ok);
            end else begin                            // single writes anywhere, any byte enables
                case ($urandom(seed) % 5)
                0: w = 22'h060000 + $urandom(seed) % 22'h10000;
                1: w = 22'h0E0000 + $urandom(seed) % 22'h20000;
                2: w = 22'h200000 + $urandom(seed) % 22'h4000;
                3: w = 22'h3FC000 + $urandom(seed) % 22'h4000;
                default: w = 22'h080000 + $urandom(seed) % 22'h20000;
                endcase
                ddr_write(w, {$urandom(seed), $urandom(seed)}, 8'($urandom(seed)), ok);
            end
            if ($urandom(seed) % 5 == 0) idle($urandom(seed) % 30);
        end
        chaos_on = 0;
        wait (!reset && !park);
        aux_on = 0; wait (aux_idle);
        // histograms and counters of the random phase
        $write("VP_HIST {\"seed\":%0d,", SEED);
        print_hist("read_first", 0); $write(","); print_hist("read_done", 1); $write(",");
        print_hist("write_accept", 2); $write(","); print_hist("aux_read_done", 3); $write(",");
        print_hist("aux_write_done", 4);
        $display(",\"reads\":%0d,\"read_words\":%0d,\"writes\":%0d,\"aux\":%0d,\"card_writes\":%0d,\"resets\":%0d,\"resets_ce_low\":%0d,\"resets_in_write\":%0d,\"parks\":%0d,\"aborted\":%0d,\"chip_commands\":%0d,\"chip_row_stalls\":%0d,\"config_writes\":%0d,\"max_ce_low_ns\":%0.1f,\"aux_max_running\":%0d,\"sim_us\":%0.1f}",
            n_reads, n_read_words, n_writes, n_aux, n_card_writes, n_resets, n_resets_ce_low, n_resets_in_write, n_parks, n_aborted,
            chip0.commands, chip0.row_stalls, chip0.config_writes, chip0.max_ce_low, aux_max_running, $realtime / 1000.0);
        // handoff: DDR writes card bytes, aux reads them; aux writes, DDR (memcard.vhd) reads
        for (i = 0; i < 64; i = i + 1) begin
            d = {8'(8*i+7), 8'(8*i+6), 8'(8*i+5), 8'(8*i+4), 8'(8*i+3), 8'(8*i+2), 8'(8*i+1), 8'(8*i)};
            ddr_write(22'h020000 + i, d, 8'hFF, ok);
        end
        idle(20);
        aux_last_valid = 0;
        for (i = 0; i < 128; i = i + 1) begin
            aux_op(1'b0, 15'(i), 32'h0, ok);
            if (aux_dout !== {8'(4*i+3), 8'(4*i+2), 8'(4*i+1), 8'(4*i)}) fail("aux does not see card byte n at DDR byte 0x100000+n");
        end
        for (i = 0; i < 256; i = i + 1) aux_op(1'b1, 15'(256 + i), {8'(4*i+3)^8'h5a, 8'(4*i+2)^8'h5a, 8'(4*i+1)^8'h5a, 8'(4*i)^8'h5a}, ok);
        ddr_read(22'h020080, 128, ok);
        if (g[vp_index(22'h020080)][7:0] !== 8'h5a) fail("handoff golden");
        // APF load / quit readback while the core is held in reset (parked)
        @(posedge clk); park <= 1'b1;
        t = now_cyc();
        while (!(all_bcr(16'h9D1F) && dut.st == 3'd5)) begin @(posedge clk); if (now_cyc() - t > 4000) fail("park"); end
        n = chip0.async_writes;
        for (i = 0; i < 512; i = i + 1) aux_op(1'b1, 15'h6000 + i, $urandom(seed), ok);
        aux_last_valid = 0;
        for (i = 0; i < 512; i = i + 1) aux_op(1'b0, 15'h6000 + i, 32'h0, ok);
        if (chip0.async_writes - n != 512 || chip1.async_reads < 512) fail("parked aux accesses not asynchronous");
        clear_hist();
        for (i = 0; i < 32; i = i + 1) aux_op(i % 2, 15'h7000 + i, $urandom(seed), ok);
        $write("VP_PARKED_AUX {"); print_hist("aux_read_done", 3); $write(","); print_hist("aux_write_done", 4); $display("}");
        park <= 1'b0;
        for (i = 0; i < 8; i = i + 1) ddr_read(22'h023000 + 64 * i, 64, ok);   // the same words, synchronous again
        // final read-back of both card windows and a VRAM sample
        for (i = 0; i < 128; i = i + 1) ddr_read(22'h020000 + 128 * i, 128, ok);
        for (i = 0; i < 128; i = i + 1) ddr_read(22'h040000 + 128 * i, 128, ok);
        for (i = 0; i < 256; i = i + 1) ddr_read(22'h000000 + 512 * i + ($urandom(seed) % 384), 128, ok);
        idle(50);
        if (card_pulses != n_card_writes) begin
            $display("card_write pulses %0d, card writes %0d", card_pulses, n_card_writes);
            fail("card_write count");
        end
        if (chip0.errors || chip1.errors) fail("chip model errors");
        if (n_resets != RESETS || n_parks != PARKS) fail("not every reset/park ran; raise N_OPS");
        $display("VRAM_PSRAM PASS");
        $finish;
    end
    initial begin #(400_000_000); fail("timeout"); end
endmodule
`default_nettype wire
