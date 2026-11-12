// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// save-readout-hold (scripts/prepare_save_readout_hold.py): the save device with rd_seen (dut)
// next to the device before the step (old; scripts/run_save_readout_hold.py renames its module to
// psx_save_exp1_old). Both get the same inputs on every clock. Checks:
//   - lockstep: every output but the read data is equal on every clk2x edge; the read data is
//     equal too except bit 1 while the address is SPU_HOLD (0x180C)
//   - reference: rd_seen equals a model built from the OLD device's aux grant (old.up_go) on every
//     edge: set by a slot read with address bit 20, cleared by a write edge at 0x180C, set wins
//   - directed, with a slot model at the Pocket's pace (one read per 2,880 clk_74a) and a model of
//     the routine's poll (write 0x180C = 1, wait a window W, read bit 1):
//       a burst of window reads: every poll window inside it reads 1
//       gaps of W/4 and W/2 between bursts: no window reads 0 (not quiet)
//       a gap of 3 W: at least one window reads 0, and the next burst reads 1 again
//       card slot reads and writes (bit 20 clear), window writes (a load's upload) and the copy
//       engine's own buffer accesses for whole windows: every window reads 0
//       a read on the aux port in the clock of the clearing write: the bit stays set
//       reads of 0x180C keep bit 0 = spu_quiet, bits 7:2 = 0
//   - random bus, APF, aux, SPU and reset traffic: lockstep and reference on every edge
`timescale 1ns/1ps
`default_nettype none
module tb_save_readout_hold;
    reg clk2x = 0, clk1x = 0, clk_74a = 0;
    always #7.38 clk2x = !clk2x;
    always @(posedge clk2x) clk1x <= !clk1x;
    always #6.73 clk_74a = !clk_74a;

    // shared inputs
    reg reset_2x = 1, core_reset = 0;
    reg [12:0] bus_addr = 0; reg bus_write = 0; reg [7:0] bus_dataWrite = 0;
    reg [11:0] timer_flags = 12'hA5C;
    reg [62:0] gpu_ranges = 63'h1234_5678_9ABC_DEF0;
    reg spu_quiet = 0; reg [31:0] spu_ss_rdata = 0;
    reg [15:0] spuram_rdata = 0; reg spuram_done = 0;
    reg [23:0] mdec_q = 24'h3C5A96;
    reg [31:0] aux_dout = 0; reg aux_done = 0;
    reg up_req = 0, up_we = 0; reg [20:0] up_addr = 0; reg [31:0] up_din = 0;
    reg savestate_start = 0, savestate_load = 0, core_held = 0;
    reg [2:0] disc = 3'd2; reg disc_settled = 1, disc_wrong = 0;
    reg [63:0] cd_state = 64'h0102_0304_C805_0118;

    // one device: its outputs as P_name, all but the read data in P_all
`define DEVICE(MOD, P, PARAMS) \
    wire [7:0] P``_q; wire P``_trap, P``_save_mode, P``_cd_freeze; wire [2:0] P``_set_sel; wire [7:0] P``_set_data; \
    wire P``_spu_hold, P``_ss_rden, P``_ss_wren, P``_spuram_req, P``_spuram_rnw, P``_mdec_tap; \
    wire [8:0] P``_ss_adr; wire [31:0] P``_ss_wdata; wire [18:0] P``_spuram_adr; wire [15:0] P``_spuram_wdata; \
    wire P``_aux_req, P``_aux_we, P``_up_done; wire [20:0] P``_aux_addr; wire [31:0] P``_aux_din, P``_up_dout; \
    wire P``_sup, P``_s_ack, P``_s_busy, P``_s_ok, P``_s_err, P``_l_ack, P``_l_busy, P``_l_ok, P``_l_err; \
    wire [31:0] P``_ss_addr, P``_ss_size, P``_ss_max; wire [2:0] P``_disc_set; wire P``_disc_tgl, P``_disc_silent; \
    MOD #PARAMS P ( \
        .clk2x(clk2x), .reset_2x(reset_2x), .core_reset(core_reset), \
        .bus_addr(bus_addr), .bus_write(bus_write), .bus_dataWrite(bus_dataWrite), .bus_dataRead(P``_q), \
        .trap(P``_trap), .save_mode(P``_save_mode), .cd_freeze(P``_cd_freeze), \
        .set_sel(P``_set_sel), .set_data(P``_set_data), .timer_flags(timer_flags), .gpu_ranges(gpu_ranges), \
        .clk1x(clk1x), .spu_hold(P``_spu_hold), .spu_quiet(spu_quiet), .spu_ss_adr(P``_ss_adr), .spu_ss_wdata(P``_ss_wdata), \
        .spu_ss_rden(P``_ss_rden), .spu_ss_wren(P``_ss_wren), .spu_ss_rdata(spu_ss_rdata), \
        .spuram_req(P``_spuram_req), .spuram_rnw(P``_spuram_rnw), .spuram_adr(P``_spuram_adr), .spuram_wdata(P``_spuram_wdata), \
        .spuram_rdata(spuram_rdata), .spuram_done(spuram_done), .mdec_tap(P``_mdec_tap), .mdec_q(mdec_q), \
        .aux_req(P``_aux_req), .aux_we(P``_aux_we), .aux_addr(P``_aux_addr), .aux_din(P``_aux_din), .aux_dout(aux_dout), .aux_done(aux_done), \
        .up_req(up_req), .up_we(up_we), .up_addr(up_addr), .up_din(up_din), .up_dout(P``_up_dout), .up_done(P``_up_done), \
        .clk_74a(clk_74a), .savestate_supported(P``_sup), .savestate_addr(P``_ss_addr), .savestate_size(P``_ss_size), \
        .savestate_maxloadsize(P``_ss_max), \
        .savestate_start(savestate_start), .savestate_start_ack(P``_s_ack), .savestate_start_busy(P``_s_busy), \
        .savestate_start_ok(P``_s_ok), .savestate_start_err(P``_s_err), \
        .savestate_load(savestate_load), .savestate_load_ack(P``_l_ack), .savestate_load_busy(P``_l_busy), \
        .savestate_load_ok(P``_l_ok), .savestate_load_err(P``_l_err), \
        .disc(disc), .disc_set(P``_disc_set), .disc_set_tgl(P``_disc_tgl), .disc_silent(P``_disc_silent), \
        .disc_settled(disc_settled), .disc_wrong(disc_wrong), .core_held(core_held), .cd_state(cd_state)); \
    wire [293:0] P``_all = {P``_trap, P``_save_mode, P``_cd_freeze, P``_set_sel, P``_set_data, \
        P``_spu_hold, P``_ss_rden, P``_ss_wren, P``_spuram_req, P``_spuram_rnw, P``_mdec_tap, \
        P``_ss_adr, P``_ss_wdata, P``_spuram_adr, P``_spuram_wdata, \
        P``_aux_req, P``_aux_we, P``_up_done, P``_aux_addr, P``_aux_din, P``_up_dout, \
        P``_sup, P``_s_ack, P``_s_busy, P``_s_ok, P``_s_err, P``_l_ack, P``_l_busy, P``_l_ok, P``_l_err, \
        P``_ss_addr, P``_ss_size, P``_ss_max, P``_disc_set, P``_disc_tgl, P``_disc_silent};

    `DEVICE(psx_save_exp1_old, old, (.ROM_HEX("tb_save_exp1_rom40.hex"), .EXT_HEX("tb_save_rom_ext.hex")))
    `DEVICE(psx_save_exp1, dut, (.ROM_HEX("tb_save_exp1_rom40.hex"), .EXT_HEX("tb_save_rom_ext.hex")))

    integer errors = 0;
    task fail(input [8*90-1:0] msg); begin
        errors = errors + 1;
        if (errors < 15) $display("ERROR t=%0t %0s (addr %h old %h new %h)", $time, msg, bus_addr, old_q, dut_q);
    end endtask

    // ---------------- the checker ----------------
    reg seen_ref = 0; reg wr_d = 0;
    integer edges = 0, hold_reads = 0, sets = 0, clears = 0, both_valid = 0;
    wire at_hold = bus_addr[12:6] == 7'b1100000 && bus_addr[5:0] == 6'h0C;
    always @(posedge clk2x) begin
        // (values before this edge)
        edges = edges + 1;
        if (old_all !== dut_all) fail("an output other than the read data differs");
        if (dut.rd_seen !== seen_ref) fail("rd_seen differs from the reference");
        if (at_hold) begin
            hold_reads = hold_reads + 1;
            if ({old_q[7:2], old_q[0]} !== {dut_q[7:2], dut_q[0]}) fail("0x180C: a bit other than bit 1 differs");
            if (dut_q[1] !== seen_ref) fail("0x180C bit 1 is not rd_seen");
            if (old_q[1] !== 1'b0) fail("old device: 0x180C bit 1 not 0");
        end else begin
            if (old_q !== dut_q) fail("read data differs outside 0x180C");
            if (old_q === dut_q && ^dut_q !== 1'bx) both_valid = both_valid + 1;
        end
        // the reference, in the device's order: the bus write, then the read on the aux port (set wins)
        if (!reset_2x) begin
            if (bus_write && !wr_d && at_hold) begin if (seen_ref) clears = clears + 1; seen_ref = 0; end
            if (old.up_go && !up_we && up_addr[20]) begin if (!seen_ref) sets = sets + 1; seen_ref = 1; end
        end
        wr_d = bus_write;
    end

    // ---------------- models ----------------
    // the aux port (psx_vram_psram): done a few clk2x after the request
    reg noise = 0; reg [2:0] aux_cnt = 0;
    always @(posedge clk2x) if (!noise) begin
        aux_done <= 1'b0;
        if (old_aux_req && !aux_done) begin
            aux_cnt <= aux_cnt + 1'b1;
            if (aux_cnt == 3'd5) begin aux_done <= 1'b1; aux_cnt <= 0; aux_dout <= $urandom; end
        end else aux_cnt <= 0;
    end
    // the slot: one access, the request held until its done (clk2x side of psx_memcard_slot)
    integer slot_accesses = 0;
    task slot(input we, input [20:0] a); begin
        @(posedge clk2x); up_we <= we; up_addr <= a; up_din <= $urandom;
        repeat (2) @(posedge clk2x);
        up_req <= 1;
        @(posedge clk2x);
        while (!old_up_done) @(posedge clk2x);
        up_req <= 0; slot_accesses = slot_accesses + 1;
    end endtask
    localparam integer PACE = 2880;       // clk_74a per bridge word, measured on hardware (3.3 MB in 32 s)
    reg [1:0] burst = 0;                   // 1 window reads, 2 card reads, 3 window writes
    reg [20:0] burst_a = 0;
    always begin
        @(posedge clk_74a);
        if (burst != 0) begin
            case (burst)
                2'd1: slot(1'b0, {1'b1, burst_a[19:0]});
                2'd2: begin slot(1'b0, {6'd0, burst_a[14:0]}); slot(1'b1, {1'b0, burst_a[19:0]}); end
                default: slot(1'b1, {1'b1, burst_a[19:0]});
            endcase
            burst_a <= burst_a + 21'h0A0A5;
            repeat (PACE - 40) @(posedge clk_74a);
        end
    end

    task bus_wr(input [12:0] a, input [7:0] d); begin
        @(posedge clk1x); bus_addr <= a; bus_dataWrite <= d;
        @(posedge clk1x); bus_write <= 1;
        @(posedge clk1x); bus_write <= 0;
    end endtask
    task bus_rd(input [12:0] a, output [7:0] d_old, output [7:0] d_new); begin
        @(posedge clk1x); bus_addr <= a;
        @(posedge clk2x); @(negedge clk2x); d_old = old_q; d_new = dut_q;
    end endtask
    // the routine's poll: clear, wait a window, read -> seen
    localparam integer W = 12000;          // clk1x: about 9 reads at the Pocket's pace
    integer windows = 0, windows_seen = 0;
    reg [7:0] o, d;
    task poll(output seen); begin
        bus_wr(13'h180C, 8'h01);
        repeat (W) @(posedge clk1x);
        bus_rd(13'h180C, o, d);
        seen = d[1]; windows = windows + 1; if (seen) windows_seen = windows_seen + 1;
        if (d[0] !== spu_quiet || d[7:2] !== 6'd0) fail("0x180C read: bit 0 or bits 7:2");
    end endtask
    task polls(input integer count, input expect_all, input expect_none); integer k, got; reg s; begin
        got = 0;
        for (k = 0; k < count; k = k + 1) begin poll(s); if (s) got = got + 1; end
        if (expect_all && got != count) fail("a poll window inside a read burst read 0");
        if (expect_none && got != 0) fail("a poll window read 1 without a window read");
    end endtask
    // a gap of G clk1x between two bursts, polled throughout: -> quiet windows seen
    task gap(input integer g, output integer quiet); integer k; reg s; begin
        quiet = 0;
        fork
            begin burst = 0; repeat (g) @(posedge clk1x); burst = 1; end
            begin for (k = 0; k < (g / W) + 3; k = k + 1) begin poll(s); if (!s) quiet = quiet + 1; end end
        join
    end endtask

    // ---------------- random traffic ----------------
    task traffic(input integer cycles); reg [12:0] a; integer k, hold; begin
        k = 0;
        while (k < cycles) begin
            @(posedge clk1x);
            case ($urandom % 10)
                0, 1:    a = 13'h1800 + ($urandom % 64);
                2, 3, 4: a = 13'h180C;
                5:       a = 13'h1C00 + ($urandom % 1024);
                6:       a = $urandom % 4096;
                7:       a = 13'h1000 + ($urandom % 2048);
                default: a = $urandom;
            endcase
            bus_addr <= a; bus_dataWrite <= $urandom; bus_write <= 0;
            hold = 1 + $urandom % 3;
            if ($urandom % 3 == 0) begin @(posedge clk1x); bus_write <= 1; repeat (hold) @(posedge clk1x); bus_write <= 0; k = k + hold + 1; end
            else begin repeat (hold) @(posedge clk1x); k = k + hold; end
            k = k + 1;
        end
    end endtask
    always @(posedge clk2x) if (noise) begin
        aux_done <= $urandom % 5 == 0; aux_dout <= $urandom;
        if ($urandom % 30 == 0) begin up_req <= $urandom; up_we <= $urandom % 4 == 0; up_addr <= {$urandom % 3 == 0, 20'd0} | ($urandom & 21'h0FFFFF); up_din <= $urandom; end
        if ($urandom % 4000 == 0) reset_2x <= 1; else reset_2x <= 0;
    end
    always @(posedge clk1x) if (noise) begin
        spuram_done <= $urandom % 7 == 0; spuram_rdata <= $urandom; spu_ss_rdata <= $urandom;
        if ($urandom % 20 == 0) spu_quiet <= $urandom;
        if ($urandom % 300 == 0) begin timer_flags <= $urandom; gpu_ranges <= {$urandom, $urandom}; cd_state <= {$urandom, $urandom}; mdec_q <= $urandom; end
        if ($urandom % 500 == 0) begin disc <= $urandom; disc_settled <= $urandom; disc_wrong <= $urandom; end
        if ($urandom % 1500 == 0) core_reset <= 1; else if ($urandom % 6 == 0) core_reset <= 0;
    end
    always @(posedge clk_74a) if (noise) begin
        if ($urandom % 400 == 0) savestate_start <= !savestate_start;
        if ($urandom % 400 == 0) savestate_load <= !savestate_load;
        if ($urandom % 3000 == 0) core_held <= !core_held;
    end

    integer n, q_short1, q_short2, q_long, same_clock = 0;
    reg s;
    initial begin
        repeat (8) @(posedge clk2x); reset_2x <= 0; repeat (4) @(posedge clk1x);
        for (n = 0; n < 2048; n = n + 1) bus_wr(13'h1000 + n, n ^ (n >> 3));
        spu_quiet <= 1;
        // nothing on the slot: quiet
        polls(3, 1'b0, 1'b1);
        // a read burst at the Pocket's pace
        burst = 1; repeat (2 * W) @(posedge clk1x);
        polls(6, 1'b1, 1'b0);
        // gaps shorter than a window: never quiet
        gap(W / 4, q_short1); polls(2, 1'b1, 1'b0);
        gap(W / 2, q_short2); polls(2, 1'b1, 1'b0);
        if (q_short1 != 0 || q_short2 != 0) fail("a gap shorter than the window read as quiet");
        // a gap of three windows: quiet at least once, then seen again
        gap(3 * W, q_long); polls(2, 1'b1, 1'b0);
        if (q_long < 1) fail("a gap of three windows never read as quiet");
        // the read-out ends: quiet from the second window on
        burst = 0; poll(s); polls(3, 1'b0, 1'b1);
        // card slot reads and writes, then window writes (an upload): not seen
        burst = 2; repeat (W) @(posedge clk1x); polls(4, 1'b0, 1'b1);
        burst = 3; repeat (W) @(posedge clk1x); polls(4, 1'b0, 1'b1);
        burst = 0; repeat (W) @(posedge clk1x);
        // the copy engine's own buffer reads and writes (address bit 20 set on the aux port): not seen
        bus_wr(13'h180C, 8'h01);
        for (n = 0; n < 4; n = n + 1) begin
            bus_wr(13'h1801, n); bus_wr(13'h1802, 3'd5); bus_wr(13'h1800, n[0] ? 8'h03 : 8'h01);
            bus_rd(13'h1800, o, d); while (d[0]) bus_rd(13'h1800, o, d);
        end
        bus_rd(13'h180C, o, d); if (d[1]) fail("the copy engine's own access set rd_seen");
        // a window read on the aux port in the clock of the clearing write: stays set
        burst = 1;
        for (n = 0; n < 40; n = n + 1) begin
            @(posedge clk2x); while (!(old.up_go && !up_we && up_addr[20])) @(posedge clk2x);
            // (clk1x phase: the write edge must fall while the access is on the port)
            bus_addr <= 13'h180C; bus_dataWrite <= 8'h01; bus_write <= 1;
            @(posedge clk2x); #1;
            if (old.up_go) begin
                same_clock = same_clock + 1;
                @(posedge clk2x); #1; if (dut.rd_seen !== 1'b1) fail("the clear won over a read on the same clock");
            end
            bus_write <= 0;
            repeat (20) @(posedge clk1x);
        end
        if (same_clock < 20) fail("too few same-clock set and clear cases");
        burst = 0; repeat (W) @(posedge clk1x);
        @(posedge clk1x); bus_write <= 0;
        // SPU_HOLD and the MDEC tap still follow the write (lockstep compares them)
        bus_wr(13'h180C, 8'h03); bus_wr(13'h180C, 8'h00); bus_wr(13'h180C, 8'h01);

        noise = 1;
        traffic(150000);
        noise = 0;
        @(posedge clk2x); reset_2x <= 0; core_reset <= 0; up_req <= 0;
        repeat (20) @(posedge clk1x);

        if (sets < 500 || clears < 500) fail("too few sets or clears");
        if (windows < 30 || windows_seen < 12) fail("too few poll windows");
        if (both_valid < 100000 || hold_reads < 10000) fail("too few valid lockstep reads");
        $display("READOUT edges=%0d slot_accesses=%0d windows=%0d windows_seen=%0d quiet_in_gaps=%0d/%0d/%0d same_clock=%0d sets=%0d clears=%0d reads_0x180C=%0d valid_lockstep_reads=%0d",
                 edges, slot_accesses, windows, windows_seen, q_short1, q_short2, q_long, same_clock, sets, clears, hold_reads, both_valid);
        if (errors == 0) $display("PASS save readout hold");
        else $display("FAIL save readout hold: %0d errors", errors);
        $finish;
    end
endmodule
`default_nettype wire
