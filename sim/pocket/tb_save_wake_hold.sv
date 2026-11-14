// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// wake-hold (scripts/prepare_save_wake_hold.py; scripts/run_save_wake_hold.py): the save-trap-timeout
// bench (commit 53421e55) with directed case W added. Three save devices on the same inputs:
//   old    the device before save-trap-timeout (renamed psx_save_exp1_old)
//   dut    the device under test, TRAP_AGE_BITS 27 (the build's 1.98 s)
//   short  the device under test, TRAP_AGE_BITS 12 (4,096 clk2x), for the directed time-out cases
// W: a wake's load arrives while the core is held in reset, is answered OK at once, and the reset stays
// held longer than the time-out and its grace: the trap must still be up when the reset ends.
// W2: the same load, the reset ends soon and the core runs without taking the trap longer than the
// time-out (the BIOS with interrupts off): the trap must stay up.
// Checks:
//   - lockstep old/dut: every output and the read data equal on every clk2x edge, through random bus,
//     APF, aux, reset and pause traffic and through the directed cases (no trap waits 2^27 clk2x here)
//   - directed on short (core running unless said):
//     A  a save whose trap is never taken: trap falls at 2^12, the Pocket gets an error 65,535 later,
//        STATUS shows no request
//     B  the same with the core paused: no answer, trap up, while paused; the time-out after it runs
//     C  ACK before the time-out: no time-out, FINISH 1 answers OK
//     D  the trap times out, an ACK comes in the grace: no error, the trap stays down until FINISH,
//        FINISH 1 answers OK
//     E  a retry (FINISH 4) whose second trap is not taken: an error
//     F  a load whose trap is never taken: an error
//     G  the core paused in the grace: no answer while paused; the error after it runs
//     H  two waits of 3,000 clk2x before an ACK (a retry between): no time-out (the age restarts)
`timescale 1ns/1ps
`define COMMA ,
`default_nettype none
module tb_save_wake_hold;
    reg clk2x = 0, clk1x = 0, clk_74a = 0;
    always #7.38 clk2x = !clk2x;
    always @(posedge clk2x) clk1x <= !clk1x;
    always #6.73 clk_74a = !clk_74a;

    reg reset_2x = 1, core_reset = 0, core_paused = 0;
    reg [12:0] bus_addr = 0; reg bus_write = 0; reg [7:0] bus_dataWrite = 0;
    reg [11:0] timer_flags = 12'hA5C;
    reg [62:0] gpu_ranges = 63'h1234_5678_9ABC_DEF0;
    reg spu_quiet = 1; reg [31:0] spu_ss_rdata = 0;
    reg [15:0] spuram_rdata = 0; reg spuram_done = 0;
    reg [23:0] mdec_q = 24'h3C5A96;
    reg [31:0] aux_dout = 0; reg aux_done = 0;
    reg up_req = 0, up_we = 0; reg [20:0] up_addr = 0; reg [31:0] up_din = 0;
    reg savestate_start = 0, savestate_load = 0, core_held = 0;
    reg [2:0] disc = 3'd2; reg disc_settled = 1, disc_wrong = 0;
    reg [63:0] cd_state = 64'h0102_0304_C805_0118;
    reg cue_busy = 0;

`define DEVICE(MOD, P, PARAMS, EXTRA) \
    wire [7:0] P``_q; wire P``_trap, P``_save_mode, P``_cd_freeze; wire [2:0] P``_set_sel; wire [7:0] P``_set_data; \
    wire P``_spu_hold, P``_ss_rden, P``_ss_wren, P``_spuram_req, P``_spuram_rnw, P``_mdec_tap; \
    wire [8:0] P``_ss_adr; wire [31:0] P``_ss_wdata; wire [18:0] P``_spuram_adr; wire [15:0] P``_spuram_wdata; \
    wire P``_aux_req, P``_aux_we, P``_up_done; wire [20:0] P``_aux_addr; wire [31:0] P``_aux_din, P``_up_dout; \
    wire P``_sup, P``_s_ack, P``_s_busy, P``_s_ok, P``_s_err, P``_l_ack, P``_l_busy, P``_l_ok, P``_l_err; \
    wire [31:0] P``_ss_addr, P``_ss_size, P``_ss_max; wire [2:0] P``_disc_set; wire P``_disc_tgl, P``_disc_silent, P``_ovf_clr; \
    MOD #PARAMS P ( \
        .clk2x(clk2x), .reset_2x(reset_2x), .core_reset(core_reset), \
        .bus_addr(bus_addr), .bus_write(bus_write), .bus_dataWrite(bus_dataWrite), .bus_dataRead(P``_q), \
        .trap(P``_trap), .save_mode(P``_save_mode), .cd_freeze(P``_cd_freeze), .cue_busy(cue_busy), \
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
        .disc_settled(disc_settled), .disc_wrong(disc_wrong), .core_held(core_held), .cd_state(cd_state), \
        .overflow(1'b0), .overflow_clear(P``_ovf_clr) EXTRA); \
    wire [294:0] P``_all = {P``_trap, P``_save_mode, P``_cd_freeze, P``_set_sel, P``_set_data, \
        P``_spu_hold, P``_ss_rden, P``_ss_wren, P``_spuram_req, P``_spuram_rnw, P``_mdec_tap, \
        P``_ss_adr, P``_ss_wdata, P``_spuram_adr, P``_spuram_wdata, \
        P``_aux_req, P``_aux_we, P``_up_done, P``_aux_addr, P``_aux_din, P``_up_dout, \
        P``_sup, P``_s_ack, P``_s_busy, P``_s_ok, P``_s_err, P``_l_ack, P``_l_busy, P``_l_ok, P``_l_err, \
        P``_ss_addr, P``_ss_size, P``_ss_max, P``_disc_set, P``_disc_tgl, P``_disc_silent, P``_ovf_clr};

    `DEVICE(psx_save_exp1_old, old, (.ROM_HEX("tb_save_exp1_rom40.hex"), .EXT_HEX("tb_save_rom_ext.hex")), )
    `DEVICE(psx_save_exp1, dut, (.ROM_HEX("tb_save_exp1_rom40.hex"), .EXT_HEX("tb_save_rom_ext.hex")), `COMMA .core_paused(core_paused))
    `DEVICE(psx_save_exp1, short, (.ROM_HEX("tb_save_exp1_rom40.hex"), .EXT_HEX("tb_save_rom_ext.hex"), .TRAP_AGE_BITS(12)), `COMMA .core_paused(core_paused))

    integer errors = 0;
    task fail(input [8*90-1:0] msg); begin
        errors = errors + 1;
        if (errors < 15) $display("ERROR t=%0t %0s (addr %h old %h new %h)", $time, msg, bus_addr, old_q, dut_q);
    end endtask

    // ---------------- lockstep old / dut ----------------
    integer edges = 0, valid_reads = 0, old_traps = 0;
    reg old_trap_d = 0;
    always @(posedge clk2x) begin
        edges = edges + 1;
        if (old_all !== dut_all) fail("lockstep: an output differs");
        if (old_q !== dut_q) fail("lockstep: the read data differs");
        if (old_q === dut_q && ^dut_q !== 1'bx) valid_reads = valid_reads + 1;
        if (old_trap && !old_trap_d) old_traps = old_traps + 1;
        old_trap_d = old_trap;
    end

    // ---------------- models ----------------
    reg noise = 0; reg [2:0] aux_cnt = 0;
    always @(posedge clk2x) if (!noise) begin
        aux_done <= 1'b0;
        if (old_aux_req && !aux_done) begin
            aux_cnt <= aux_cnt + 1'b1;
            if (aux_cnt == 3'd5) begin aux_done <= 1'b1; aux_cnt <= 0; aux_dout <= $urandom; end
        end else aux_cnt <= 0;
    end
    task bus_wr(input [12:0] a, input [7:0] d); begin
        @(posedge clk1x); bus_addr <= a; bus_dataWrite <= d;
        @(posedge clk1x); bus_write <= 1;
        @(posedge clk1x); bus_write <= 0;
    end endtask
    task bus_rd_short(input [12:0] a, output [7:0] d); begin
        @(posedge clk1x); bus_addr <= a;
        @(posedge clk2x); @(negedge clk2x); d = short_q;
    end endtask
    task traffic(input integer cycles); reg [12:0] a; integer k, hold; begin
        k = 0;
        while (k < cycles) begin
            @(posedge clk1x);
            case ($urandom % 10)
                0, 1, 2: a = 13'h1800 + ($urandom % 64);
                3, 4:    a = 13'h1803 + ($urandom % 2);       // FINISH / ACK
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
        if ($urandom % 30 == 0) begin up_req <= $urandom; up_we <= $urandom % 4 == 0; up_addr <= $urandom; up_din <= $urandom; end
        if ($urandom % 4000 == 0) reset_2x <= 1; else reset_2x <= 0;
    end
    always @(posedge clk1x) if (noise) begin
        spuram_done <= $urandom % 7 == 0; spuram_rdata <= $urandom; spu_ss_rdata <= $urandom;
        if ($urandom % 20 == 0) spu_quiet <= $urandom;
        if ($urandom % 300 == 0) begin timer_flags <= $urandom; gpu_ranges <= {$urandom, $urandom}; cd_state <= {$urandom, $urandom}; mdec_q <= $urandom; end
        if ($urandom % 500 == 0) begin disc <= $urandom; disc_settled <= $urandom; disc_wrong <= $urandom; end
        if ($urandom % 1500 == 0) core_reset <= 1; else if ($urandom % 6 == 0) core_reset <= 0;
        if ($urandom % 700 == 0) core_paused <= !core_paused;
    end
    always @(posedge clk_74a) if (noise) begin
        if ($urandom % 400 == 0) savestate_start <= !savestate_start;
        if ($urandom % 400 == 0) savestate_load <= !savestate_load;
        if ($urandom % 3000 == 0) core_held <= !core_held;
        if ($urandom % 97 == 0) cue_busy <= !cue_busy;
    end

    // ---------------- directed (short) ----------------
    localparam integer AGE = 4096, GRACE = 65535;
    task request(input is_load); begin
        @(posedge clk_74a); if (is_load) savestate_load <= 1; else savestate_start <= 1;
        repeat (6) @(posedge clk_74a); savestate_load <= 0; savestate_start <= 0;
        while (!short_trap) @(posedge clk2x);
    end endtask
    // clk2x until the Pocket's busy falls (limit), -> cycles, or -1
    task answer(input is_load, input integer limit, output integer took); integer k; begin
        took = -1;
        for (k = 0; k < limit && took < 0; k = k + 1) begin
            @(posedge clk2x);
            if (!(is_load ? short_l_busy : short_s_busy)) took = k;
        end
    end endtask
    task expect_error(input is_load, input integer limit, input [8*40-1:0] what); integer t; reg [7:0] st; begin
        answer(is_load, limit, t);
        if (t < 0) fail({what, ": no answer"});
        else if ((is_load ? short_l_err : short_s_err) !== 1'b1 || (is_load ? short_l_ok : short_s_ok) !== 1'b0) fail({what, ": not an error"});
        bus_rd_short(13'h1800, st);
        if (st[2:1] !== 2'b00) fail({what, ": a request still shown"});
        if (short_trap) fail({what, ": trap up after the answer"});
    end endtask
    task expect_busy(input is_load, input integer cycles, input [8*40-1:0] what); integer t; begin
        answer(is_load, cycles, t);
        if (t >= 0) fail({what, ": answered"});
    end endtask
    task finish_ok(input is_load, input [8*40-1:0] what); integer t; begin
        bus_wr(13'h1803, 8'h01);
        answer(is_load, 2000, t);
        if (t < 0 || (is_load ? short_l_ok : short_s_ok) !== 1'b1) fail({what, ": FINISH 1 not answered OK"});
    end endtask
    // the trap must stay down from now until FINISH: count rises
    reg watch = 0; integer rises_watched = 0; reg st_d = 0;
    always @(posedge clk2x) begin
        if (watch && short_trap && !st_d) rises_watched = rises_watched + 1;
        st_d = short_trap;
    end

    integer t, tfall, k, cases = 0;
    initial begin
        repeat (8) @(posedge clk2x); reset_2x <= 0; repeat (4) @(posedge clk1x);
        for (k = 0; k < 2048; k = k + 1) bus_wr(13'h1000 + k, k ^ (k >> 3));

        noise = 1;
        traffic(120000);
        noise = 0;
        @(posedge clk2x); reset_2x <= 0; core_reset <= 0; up_req <= 0; core_held <= 0; core_paused <= 0;
        savestate_start <= 0; savestate_load <= 0;
        repeat (200) @(posedge clk1x);
        // end whatever request the traffic left open
        if (short_s_busy || short_l_busy || short_trap) begin
            bus_wr(13'h1804, 0); bus_wr(13'h1803, 8'h02); repeat (200) @(posedge clk2x);
        end
        repeat (2 * GRACE) @(posedge clk2x);
        if (short_s_busy || short_l_busy || short_trap) fail("directed: the device did not settle");

        // A
        request(0); t = 0;
        while (short_trap && t < 2 * AGE) begin @(posedge clk2x); t = t + 1; end
        if (t < AGE - 4 || t > AGE + 4) fail("A: the trap did not fall at 2^12");
        expect_error(0, GRACE + 2000, "A"); cases = cases + 1;
        // B
        core_paused <= 1; request(0);
        expect_busy(0, 3 * (AGE + GRACE), "B paused");
        if (!short_trap) fail("B: trap down while paused");
        core_paused <= 0;
        expect_error(0, AGE + GRACE + 2000, "B"); cases = cases + 1;
        // C
        request(0); repeat (1000) @(posedge clk2x); bus_wr(13'h1804, 0);
        expect_busy(0, AGE + GRACE + 2000, "C");
        finish_ok(0, "C"); cases = cases + 1;
        // D
        request(0); while (short_trap) @(posedge clk2x);
        repeat (200) @(posedge clk2x); bus_wr(13'h1804, 0); rises_watched = 0; watch = 1;
        expect_busy(0, GRACE + 4000, "D");
        watch = 0; if (rises_watched != 0) fail("D: the trap came back after the ACK");
        finish_ok(0, "D"); cases = cases + 1;
        // E
        request(0); bus_wr(13'h1804, 0); bus_wr(13'h1803, 8'h04);
        while (!short_trap) @(posedge clk2x);
        expect_error(0, AGE + GRACE + 2000, "E"); cases = cases + 1;
        // F
        request(1); expect_error(1, AGE + GRACE + 2000, "F"); cases = cases + 1;
        // G
        request(0); while (short_trap) @(posedge clk2x);
        repeat (100) @(posedge clk2x); core_paused <= 1;
        expect_busy(0, 3 * GRACE, "G paused");
        core_paused <= 0;
        expect_error(0, GRACE + 2000, "G"); cases = cases + 1;
        // H
        request(0); repeat (3000) @(posedge clk2x); bus_wr(13'h1804, 0); bus_wr(13'h1803, 8'h04);
        while (!short_trap) @(posedge clk2x);
        repeat (3000) @(posedge clk2x); bus_wr(13'h1804, 0);
        if (!short_s_busy) fail("H: answered before FINISH");
        finish_ok(0, "H"); cases = cases + 1;

        // W (a wake): the load arrives while the Pocket holds the core in reset (answered OK at once,
        // runs when the CPU takes the trap after the reset); the reset stays held longer than the time-out
        // and its grace; when it ends the trap must still be up for the CPU
        core_held <= 1; core_reset <= 1; repeat (20) @(posedge clk2x);
        request(1); repeat (20) @(posedge clk2x);
        if (short_l_busy || short_l_ok !== 1'b1) fail("W: the held load was not answered OK at once");
        repeat (AGE + GRACE + 2000) @(posedge clk2x);
        core_reset <= 0; core_held <= 0; repeat (50) @(posedge clk2x);
        if (!short_trap) fail("W: a wake's held load timed out while the core was held in reset");
        else $display("TRAP W: the held load's trap survived the reset hold");
        cases = cases + 1;
        bus_wr(13'h1804, 0); bus_wr(13'h1803, 8'h02); repeat (2 * GRACE) @(posedge clk2x);
        // W2 (a wake, part 2): the held load's reset ends soon, then the core runs without taking the trap
        // (the BIOS with interrupts off) longer than the time-out and its grace: the trap must stay up
        core_held <= 1; core_reset <= 1; repeat (20) @(posedge clk2x);
        request(1); repeat (200) @(posedge clk2x);
        core_reset <= 0; core_held <= 0;
        repeat (AGE + GRACE + 2000) @(posedge clk2x);
        if (!short_trap) fail("W2: a wake's load timed out while the core ran without taking the trap");
        else $display("TRAP W2: the wake's trap stayed up for the BIOS");
        cases = cases + 1;
        bus_wr(13'h1804, 0); bus_wr(13'h1803, 8'h02); repeat (2 * GRACE) @(posedge clk2x);
        if (valid_reads < 50000 || old_traps < 20) fail("too little lockstep traffic");
        $display("TRAP edges=%0d valid_lockstep_reads=%0d old_traps=%0d directed_cases=%0d", edges, valid_reads, old_traps, cases);
        if (errors == 0) $display("PASS save wake hold");
        else $display("FAIL save wake hold: %0d errors", errors);
        $finish;
    end
endmodule
`default_nettype wire
