// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// save-rom-ext (scripts/prepare_save_rom_ext.py): the save device with the extension ROM (dut)
// next to the device before the step (old; scripts/run_save_rom_ext.py renames its module to
// psx_save_exp1_old). Both get the same inputs on every clock. Checks:
//   - lockstep: every output but the read data is equal on every clk2x edge, in all three
//     phases; the read data is equal too unless the address is in the second ROM's window
//     (0x1C00-0x1FFF) while the bank is set
//   - phase A, random bus, APF, aux, SPU and reset traffic where no SAVE_MODE write has bit 1:
//     the bank never sets and the read data is equal on every clk2x edge
//   - phase B, the same traffic with any SAVE_MODE byte: the bank follows bit 1 of the last
//     SAVE_MODE write (0 after either reset); a window read with the bank set gives the
//     extension's byte, one clk2x after the address like the second ROM
//   - directed: every extension address (0-1023) reads its byte of the hex file with
//     SAVE_MODE = 3 and with SAVE_MODE = 2; the first ROM, the page and the registers read as
//     in the old device meanwhile; with SAVE_MODE = 1 every second-ROM byte reads as before;
//     reset_2x and the core reset edge clear the bank; other register writes leave it
`timescale 1ns/1ps
`default_nettype none
module tb_save_rom_ext;
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

    `DEVICE(psx_save_exp1_old, old, (.ROM_HEX("tb_save_exp1_rom40.hex")))
    `DEVICE(psx_save_exp1, dut, (.ROM_HEX("tb_save_exp1_rom40.hex"), .EXT_HEX("tb_save_rom_ext.hex")))

    reg [7:0] rom_ref [0:4095];
    reg [7:0] rom2_ref [0:1023];
    reg [7:0] ext_ref [0:1023];
    initial begin
        $readmemh("tb_save_exp1_rom.hex", rom_ref);
        $readmemh("tb_save_exp1_rom2.hex", rom2_ref);
        $readmemh("tb_save_rom_ext.hex", ext_ref);
    end

    integer errors = 0;
    task fail(input [8*90-1:0] msg); begin
        errors = errors + 1;
        if (errors < 15) $display("ERROR t=%0t %0s (addr %h old %h new %h)", $time, msg, bus_addr, old_q, dut_q);
    end endtask

    // ---------------- the checker ----------------
    // what the bank must be: bit 1 of the last SAVE_MODE write, 0 after reset_2x or a core reset edge
    // (modelled with the device's own strobes: the write edge and the synchronised reset edge)
    reg bank_ref = 0; reg wr_d = 0; reg [2:0] core_s = 0;
    integer phase = 0;                  // 0 = idle, 1 = A, 2 = B, 3 = directed
    integer edges = 0, window_checks = 0, ext_values = 0, bank_sets = 0, both_valid = 0;
    reg [12:0] addr_was = 0;
    wire window = bus_addr[12:10] == 3'b111;
    always @(posedge clk2x) begin
        // (values before this edge)
        edges = edges + 1;
        if (old_all !== dut_all) fail("an output other than the read data differs");
        if (dut.rom_bank !== bank_ref) fail("the bank is not bit 1 of the last SAVE_MODE write");
        if (phase == 1 && dut.rom_bank !== 1'b0) fail("the bank is set although no SAVE_MODE write had bit 1");
        if (!(window && bank_ref)) begin
            if (old_q !== dut_q) fail("read data differs outside the extension");
            if (old_q === dut_q && ^dut_q !== 1'bx) both_valid = both_valid + 1;
        end else if (bus_addr === addr_was) begin
            // the address was the same at the edge before: the ROM's output register holds its byte
            window_checks = window_checks + 1;
            if (dut_q !== ext_ref[bus_addr[9:0]]) fail("extension byte wrong");
            if (old_q !== rom2_ref[bus_addr[9:0]]) fail("old device: second ROM byte wrong");
            if (ext_ref[bus_addr[9:0]] !== rom2_ref[bus_addr[9:0]]) ext_values = ext_values + 1;
        end
        addr_was = bus_addr;
        // the reference bank, in the device's order: the bus write, then the core reset edge; reset_2x over both
        if (bus_write && !wr_d && bus_addr == 13'h180E) begin
            if (bus_dataWrite[1] && !bank_ref) bank_sets = bank_sets + 1;
            bank_ref = bus_dataWrite[1];
        end
        if (core_s[1] && !core_s[2]) bank_ref = 0;
        if (reset_2x) bank_ref = 0;
        wr_d = bus_write; core_s = {core_s[1:0], core_reset};
    end

    // ---------------- random traffic (phases A and B) ----------------
    integer n;
    task traffic(input integer cycles, input allow_bank); reg [12:0] a; reg [7:0] d; integer k, hold; begin
        k = 0;
        while (k < cycles) begin
            @(posedge clk1x);
            case ($urandom % 10)
                0, 1, 2: a = 13'h1800 + ($urandom % 64);
                3:       a = 13'h180E;
                4, 5:    a = 13'h1C00 + ($urandom % 1024);
                6:       a = $urandom % 4096;
                7:       a = 13'h1000 + ($urandom % 2048);
                default: a = $urandom;
            endcase
            d = $urandom;
            if (!allow_bank && a == 13'h180E) d[1] = 1'b0;
            bus_addr <= a; bus_dataWrite <= d; bus_write <= 0;
            hold = 1 + $urandom % 3;
            if ($urandom % 3 == 0) begin @(posedge clk1x); bus_write <= 1; repeat (hold) @(posedge clk1x); bus_write <= 0; k = k + hold + 1; end
            else begin repeat (hold) @(posedge clk1x); k = k + hold; end
            k = k + 1;
        end
    end endtask
    reg noise = 0;
    always @(posedge clk2x) if (noise) begin
        aux_done <= $urandom % 5 == 0; aux_dout <= $urandom;
        if ($urandom % 50 == 0) begin up_req <= $urandom; up_we <= $urandom; up_addr <= $urandom; up_din <= $urandom; end
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

    // ---------------- directed ----------------
    task bus_wr(input [12:0] a, input [7:0] d); begin
        @(posedge clk1x); bus_addr <= a; bus_dataWrite <= d;
        @(posedge clk1x); bus_write <= 1;
        @(posedge clk1x); bus_write <= 0;
    end endtask
    // one clk2x after the address, as memorymux takes it
    task bus_rd(input [12:0] a, output [7:0] d_old, output [7:0] d_new); begin
        @(posedge clk1x); bus_addr <= a;
        @(posedge clk2x); @(negedge clk2x); d_old = old_q; d_new = dut_q;
    end endtask
    integer ext_read = 0, rom2_read = 0, rom1_read = 0;
    task all_ext(input [7:0] mode); integer i; reg [7:0] o, d; begin
        bus_wr(13'h180E, mode);
        for (i = 0; i < 1024; i = i + 1) begin
            bus_rd(13'h1C00 + i, o, d); ext_read = ext_read + 1;
            if (d !== ext_ref[i]) fail("directed: extension byte");
            if (o !== rom2_ref[i]) fail("directed: old device second ROM byte");
        end
    end endtask
    task all_rom(input [7:0] mode); integer i; reg [7:0] o, d; begin
        bus_wr(13'h180E, mode);
        for (i = 0; i < 1024; i = i + 1) begin
            bus_rd(13'h1C00 + i, o, d); rom2_read = rom2_read + 1;
            if (d !== rom2_ref[i] || o !== rom2_ref[i]) fail("directed: second ROM byte");
        end
    end endtask
    task all_first(input [7:0] mode); integer i; reg [7:0] o, d; begin
        bus_wr(13'h180E, mode);
        for (i = 0; i < 4096; i = i + 1) begin
            bus_rd(i, o, d); rom1_read = rom1_read + 1;
            if (d !== rom_ref[i] || o !== rom_ref[i]) fail("directed: first ROM byte");
        end
    end endtask
    reg [7:0] o, d;

    initial begin
        repeat (8) @(posedge clk2x); reset_2x <= 0; repeat (4) @(posedge clk1x);
        // the staging page: known bytes in both devices
        for (n = 0; n < 2048; n = n + 1) bus_wr(13'h1000 + n, n ^ (n >> 3));

        phase = 3;
        all_rom(8'd0); all_rom(8'd1);
        all_ext(8'd3); all_first(8'd3);
        if (dut_save_mode !== 1'b1) fail("SAVE_MODE = 3 does not set save_mode");
        // the page and the registers with the bank set (lockstep compares the read data)
        for (n = 13'h1000; n < 13'h1C00; n = n + 7) bus_rd(n, o, d);
        // other register writes leave the bank
        bus_wr(13'h180F, 8'hFF); bus_wr(13'h180C, 8'h01); bus_wr(13'h180D, 8'hFF); bus_wr(13'h1801, 8'hFF); bus_wr(13'h1C0E, 8'h00);
        bus_wr(13'h180C, 8'h00); bus_wr(13'h180F, 8'h00);
        bus_rd(13'h1C05, o, d); if (d !== ext_ref[5]) fail("another register write cleared the bank");
        // SAVE_MODE = 1 again: the second ROM is back
        all_rom(8'd1);
        // the bank alone
        all_ext(8'd2);
        if (dut_save_mode !== 1'b0) fail("SAVE_MODE = 2 sets save_mode");
        // the core reset edge clears it
        bus_wr(13'h180E, 8'd3);
        @(posedge clk1x); core_reset <= 1; repeat (6) @(posedge clk1x); core_reset <= 0; repeat (4) @(posedge clk1x);
        if (dut.rom_bank !== 1'b0 || dut_save_mode !== 1'b0) fail("the core reset edge left the bank or save_mode");
        bus_rd(13'h1FFF, o, d); if (d !== rom2_ref[1023]) fail("second ROM not back after the core reset");
        // reset_2x clears it
        bus_wr(13'h180E, 8'd3);
        @(posedge clk2x); reset_2x <= 1; repeat (4) @(posedge clk2x); reset_2x <= 0; repeat (4) @(posedge clk1x);
        if (dut.rom_bank !== 1'b0) fail("reset_2x left the bank");
        bus_rd(13'h1C00, o, d); if (d !== rom2_ref[0]) fail("second ROM not back after reset_2x");

        phase = 1; noise = 1; bank_sets = 0;
        traffic(60000, 1'b0);
        if (bank_sets != 0) fail("phase A set the bank");
        phase = 2;
        traffic(120000, 1'b1);
        noise = 0; phase = 0;
        @(posedge clk2x); reset_2x <= 0; core_reset <= 0;
        repeat (20) @(posedge clk1x);

        if (ext_read != 2048 || rom2_read != 3072 || rom1_read != 4096) fail("directed read counts");
        if (bank_sets < 100) fail("phase B hardly set the bank");
        if (window_checks < 5000 || ext_values < 4000) fail("too few extension reads in the random phases");
        if (both_valid < 100000) fail("too few valid lockstep reads");
        $display("ROMEXT edges=%0d directed ext=%0d rom2=%0d rom1=%0d bank_sets=%0d window_checks=%0d differing_from_rom2=%0d valid_lockstep_reads=%0d",
                 edges, ext_read, rom2_read, rom1_read, bank_sets, window_checks, ext_values, both_valid);
        if (errors == 0) $display("PASS save rom ext");
        else $display("FAIL save rom ext: %0d errors", errors);
        $finish;
    end
endmodule
`default_nettype wire
