// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// psx_save_exp1: the EXP1 device and APF handshake for Pocket save states.
// A memorymux-like byte bus on clk1x (clk2x / 2), an aux port model with
// random latency, a card-slot client issuing random aux traffic at the same
// time, and the APF handshake on clk_74a. Checks:
//   - an APF save raises trap, STATUS shows save, ACK drops trap
//   - ROM bytes read back (ROM_HEX)
//   - pages written over the bus are copied word for word into the save
//     buffer at page*512 + word with aux_addr[20] set, at the two ends of
//     the buffer and in between, while the card client runs
//   - pages fetched from the buffer read back over the bus byte for byte
//   - FINISH OK / error reach savestate_start_ok / _err, and a load request
//     ends in savestate_load_ok
//   - every card access completes with its own data and never overlaps a copy
//   - the hidden-state taps: timer flags read back live, set values reach
//     set_irq/set_dma/set_timer and apply only while SET_GO is 1
//   - SPU tap: hold reaches the SPU and quiet reads back; SS reads and writes
//     go out as single clk1x strobes with the address and data; staging
//     pages copy to and from SPU RAM pages halfword for halfword through the
//     RAM port (random latency), ignoring done pulses of the SPU's own
//     accesses between ours
`timescale 1ns/1ps
`default_nettype none
module tb_save_exp1;
    reg clk2x = 0, clk1x = 0, clk_74a = 0;
    always #7.38 clk2x = !clk2x;
    always @(posedge clk2x) clk1x <= !clk1x;
    always #6.73 clk_74a = !clk_74a;

    reg reset_2x = 1;
    reg [12:0] bus_addr = 0; reg bus_write = 0; reg [7:0] bus_dataWrite = 0;
    wire [7:0] bus_dataRead; wire trap;
    wire aux_req, aux_we; wire [20:0] aux_addr; wire [31:0] aux_din; reg [31:0] aux_dout = 0; reg aux_done = 0;
    reg up_req = 0, up_we = 0; reg [20:0] up_addr = 0; reg [31:0] up_din = 0; wire [31:0] up_dout; wire up_done;
    reg savestate_start = 0, savestate_load = 0;
    wire supported, start_ack, start_busy, start_ok, start_err, load_ack, load_busy, load_ok, load_err;
    wire [31:0] ss_addr, ss_size, ss_max;
    wire set_go; wire [10:0] set_irq; wire [6:0] set_dma; wire [11:0] set_timer;
    reg [11:0] timer_flags = 12'hA5C;
    reg [62:0] gpu_ranges = 63'h1234_5678_9ABC_DEF0;
    wire [63:0] gpu_ref = {1'b0, gpu_ranges};
    wire spu_hold, spu_ss_rden, spu_ss_wren, spuram_req, spuram_rnw;
    wire [8:0] spu_ss_adr; wire [31:0] spu_ss_wdata; reg [31:0] spu_ss_rdata = 0;
    wire [18:0] spuram_adr; wire [15:0] spuram_wdata; reg [15:0] spuram_rdata = 0; reg spuram_done = 0;
    reg [3:0] quiet_d = 0; wire spu_quiet = quiet_d[3];

    psx_save_exp1 #(.ROM_HEX("tb_save_exp1_rom.hex")) dut (
        .clk2x(clk2x), .reset_2x(reset_2x),
        .bus_addr(bus_addr), .bus_write(bus_write), .bus_dataWrite(bus_dataWrite), .bus_dataRead(bus_dataRead), .trap(trap),
        .set_go(set_go), .set_irq(set_irq), .set_dma(set_dma), .set_timer(set_timer), .timer_flags(timer_flags), .gpu_ranges(gpu_ranges),
        .clk1x(clk1x), .spu_hold(spu_hold), .spu_quiet(spu_quiet), .spu_ss_adr(spu_ss_adr), .spu_ss_wdata(spu_ss_wdata),
        .spu_ss_rden(spu_ss_rden), .spu_ss_wren(spu_ss_wren), .spu_ss_rdata(spu_ss_rdata),
        .spuram_req(spuram_req), .spuram_rnw(spuram_rnw), .spuram_adr(spuram_adr), .spuram_wdata(spuram_wdata),
        .spuram_rdata(spuram_rdata), .spuram_done(spuram_done),
        .aux_req(aux_req), .aux_we(aux_we), .aux_addr(aux_addr), .aux_din(aux_din), .aux_dout(aux_dout), .aux_done(aux_done),
        .up_req(up_req), .up_we(up_we), .up_addr(up_addr), .up_din(up_din), .up_dout(up_dout), .up_done(up_done),
        .clk_74a(clk_74a), .savestate_supported(supported), .savestate_addr(ss_addr), .savestate_size(ss_size),
        .savestate_maxloadsize(ss_max),
        .savestate_start(savestate_start), .savestate_start_ack(start_ack), .savestate_start_busy(start_busy),
        .savestate_start_ok(start_ok), .savestate_start_err(start_err),
        .savestate_load(savestate_load), .savestate_load_ack(load_ack), .savestate_load_busy(load_busy),
        .savestate_load_ok(load_ok), .savestate_load_err(load_err));

    integer errors = 0;
    task fail(input [8*80-1:0] msg); begin errors = errors + 1; if (errors < 15) $display("ERROR t=%0t %0s", $time, msg); end endtask

    // ---------------- aux port model (random latency, holds req until done) ----------------
    reg [31:0] card_mem [0:255];
    reg [31:0] save_mem [0:1048575];
    initial for (integer i = 0; i < 1048576; i = i + 1) save_mem[i] = 32'hDEAD0000 ^ i;
    integer lat = 0, aux_count = 0, card_mem_ops = 0;
    reg aux_busy = 0; reg [31:0] din_start = 0;
    always @(posedge clk2x) begin
        aux_done <= 0;
        if (aux_req && !aux_busy && !aux_done) begin aux_busy <= 1; lat = 3 + $urandom % 40; din_start = aux_din; end
        else if (aux_busy) begin
            // a controller may take the write data on any cycle of the request
            if (aux_we && aux_din !== din_start) fail("aux write data changed during the request");
            if (lat > 0) lat = lat - 1;
            else begin
                aux_done <= 1; aux_busy <= 0; aux_count = aux_count + 1;
                if (aux_addr[20]) begin
                    if (aux_we) save_mem[aux_addr[19:0]] = aux_din; else aux_dout <= save_mem[aux_addr[19:0]];
                end else begin
                    if (aux_addr[19:8] != 0) fail("card access outside the card words");
                    if (aux_we) card_mem[aux_addr[7:0]] = aux_din; else aux_dout <= card_mem[aux_addr[7:0]];
                end
            end
        end
        if (aux_busy && !aux_req) fail("aux_req dropped before aux_done");
        if (dut.own_req && dut.up_go) fail("copy and card on the aux port together");
    end

    // ---------------- SPU model (clk1x) ----------------
    reg [31:0] ss_regs [0:511];
    reg [15:0] spu_ram [0:262143];
    initial for (integer i = 0; i < 512; i = i + 1) ss_regs[i] = 32'h5500_0000 ^ (i * 32'h01010101);
    initial for (integer i = 0; i < 262144; i = i + 1) spu_ram[i] = i[15:0] ^ 16'hA5A5;
    integer ss_strobes = 0, sp_lat = -1, spurious = 0; reg spu_noise = 0; reg [18:0] sp_adr; reg sp_rnw; reg [15:0] sp_wd;
    reg ss_rden_d = 0, ss_wren_d = 0;
    always @(posedge clk1x) begin
        quiet_d <= {quiet_d[2:0], spu_hold};
        ss_rden_d <= spu_ss_rden; ss_wren_d <= spu_ss_wren;
        if ((spu_ss_rden && ss_rden_d) || (spu_ss_wren && ss_wren_d)) fail("SS strobe longer than one clk1x");
        if (spu_ss_rden && spu_ss_wren) fail("SS read and write together");
        if (spu_ss_rden) begin spu_ss_rdata <= ss_regs[spu_ss_adr]; ss_strobes = ss_strobes + 1; end
        if (spu_ss_wren) begin ss_regs[spu_ss_adr] <= spu_ss_wdata; ss_strobes = ss_strobes + 1; end
        spuram_done <= 0;
        if (spuram_req) begin
            if (sp_lat >= 0) fail("SPU RAM request while one is open");
            if (!spu_quiet) fail("SPU RAM request while the SPU is not quiet");
            sp_lat = 1 + $urandom % 12; sp_adr = spuram_adr; sp_rnw = spuram_rnw; sp_wd = spuram_wdata;
        end else if (sp_lat > 0) sp_lat = sp_lat - 1;
        else if (sp_lat == 0) begin
            sp_lat = -1; spuram_done <= 1;
            if (sp_adr[0]) fail("odd SPU RAM address");
            if (sp_rnw) spuram_rdata <= spu_ram[sp_adr[18:1]]; else spu_ram[sp_adr[18:1]] = sp_wd;
        end else if (spu_noise && $urandom % 9 == 0) begin
            spuram_done <= 1; spuram_rdata <= $urandom; spurious = spurious + 1;   // the SPU's own access
        end
    end

    // ---------------- card-slot client: random accesses to card words ----------------
    reg card_on = 0; integer card_done_n = 0;
    reg [31:0] card_ref [0:255];
    initial for (integer i = 0; i < 256; i = i + 1) card_ref[i] = 32'hC0DE0000 + i;
    always begin : card
        integer w; reg we; reg [31:0] d;
        @(posedge clk2x);
        if (card_on && $urandom % 4 == 0) begin
            w = $urandom % 256; we = $urandom % 2; d = $urandom;
            up_addr <= w; up_we <= we; up_din <= d; up_req <= 1;
            @(posedge clk2x); while (!up_done) @(posedge clk2x);
            if (we) card_ref[w] = d;
            else if (up_dout !== card_ref[w]) fail("card read wrong data");
            up_req <= 0; card_done_n = card_done_n + 1;
            repeat ($urandom % 20) @(posedge clk2x);
        end
    end
    initial for (integer i = 0; i < 256; i = i + 1) card_mem[i] = 32'hC0DE0000 + i;

    // ---------------- memorymux-like EXP1 byte bus (clk1x) ----------------
    task bus_wr(input [12:0] a, input [7:0] d); begin
        @(posedge clk1x); bus_addr <= a; bus_dataWrite <= d;
        @(posedge clk1x); bus_write <= 1;
        @(posedge clk1x); bus_write <= 0;
        repeat ($urandom % 3) @(posedge clk1x);
    end endtask
    task bus_rd(input [12:0] a, output [7:0] d); begin
        @(posedge clk1x); bus_addr <= a;
        repeat (2) @(posedge clk1x); d = bus_dataRead;
    end endtask
    task wait_copy; reg [7:0] s; begin
        s = 1; while (s[0]) bus_rd(13'h1800, s);
    end endtask

    // ---------------- APF (clk_74a) ----------------
    // like core_bridge_cmd: hold start/load until the core acks, then drop it
    task apf_start(input load); integer n; begin
        @(posedge clk_74a); if (load) savestate_load <= 1; else savestate_start <= 1;
        n = 0;
        do begin @(posedge clk_74a); n = n + 1; end while (!(load ? load_ack : start_ack) && n < 2000);
        if (n >= 2000) fail(load ? "no load_ack" : "no start_ack");
        savestate_load <= 0; savestate_start <= 0;
    end endtask

    reg [7:0] rom_ref [0:4095];
    reg [7:0] pg [0:2047];
    integer p, i, k, pages [0:5], bad;
    reg [7:0] b, st;
    initial begin
        $readmemh("tb_save_exp1_rom.hex", rom_ref);
        repeat (10) @(posedge clk2x); reset_2x <= 0;
        if (!supported || ss_addr != 32'h3000_0000 || ss_size != 32'h40_0000 || ss_max != 32'h40_0000) fail("APF query values");
        card_on = 1;

        // save request -> trap, status, ack
        apf_start(0);
        wait (trap);
        bus_rd(13'h1800, st); if (st[2:1] !== 2'b01) fail("STATUS does not show save");
        bus_wr(13'h1804, 8'h01);
        repeat (4) @(posedge clk2x); if (trap) fail("ACK did not drop trap");
        if (!start_busy) fail("start_busy low while the routine runs");

        // ROM
        for (i = 0; i < 4096; i = i + 37) begin bus_rd(i, b); if (b !== rom_ref[i]) fail("ROM byte"); end
        bus_rd(4095, b); if (b !== rom_ref[4095]) fail("ROM last byte");

        // pages to the buffer: both ends and between
        pages[0] = 0; pages[1] = 2047; pages[2] = 1; pages[3] = 1023; pages[4] = 1024; pages[5] = 2046;
        for (p = 0; p < 6; p = p + 1) begin
            for (i = 0; i < 2048; i = i + 1) begin pg[i] = $urandom; bus_wr(13'h1000 + i, pg[i]); end
            bus_wr(13'h1801, pages[p][7:0]); bus_wr(13'h1802, pages[p][10:8]);
            bus_wr(13'h1800, 8'h01);
            wait_copy;
            bad = 0;
            for (k = 0; k < 512; k = k + 1)
                if (save_mem[{pages[p][10:0], k[8:0]}] !== {pg[4*k+3], pg[4*k+2], pg[4*k+1], pg[4*k]}) bad = bad + 1;
            if (bad) begin $display("page %0d: %0d words wrong", pages[p], bad); fail("page to buffer"); end
        end
        // buffer to pages
        for (p = 0; p < 6; p = p + 1) begin
            for (k = 0; k < 512; k = k + 1) save_mem[{pages[p][10:0], k[8:0]}] = $urandom;
            bus_wr(13'h1801, pages[p][7:0]); bus_wr(13'h1802, pages[p][10:8]);
            bus_wr(13'h1800, 8'h03);
            wait_copy;
            bad = 0;
            for (i = 0; i < 2048; i = i + 1) begin
                bus_rd(13'h1000 + i, b);
                if (b !== save_mem[{pages[p][10:0], i[10:2]}][8*i[1:0] +: 8]) bad = bad + 1;
            end
            if (bad) begin $display("page %0d: %0d bytes wrong", pages[p], bad); fail("buffer to page"); end
        end
        bus_wr(13'h1803, 8'h01);
        fork begin wait (start_ok); end begin repeat (2000) @(posedge clk2x); fail("no start_ok"); end join_any disable fork;
        if (start_err || start_busy) fail("start flags after OK");
        bus_rd(13'h1800, st); if (st[2:1] !== 2'b00) fail("STATUS still shows a request");

        // a save that fails
        apf_start(0); wait (trap); bus_wr(13'h1804, 8'h01); bus_wr(13'h1803, 8'h02);
        fork begin wait (start_err); end begin repeat (2000) @(posedge clk2x); fail("no start_err"); end join_any disable fork;
        if (start_ok) fail("start_ok with an error");

        // a save that retries once (not at a quiet point), then succeeds
        apf_start(0); wait (trap); bus_wr(13'h1804, 8'h01); bus_wr(13'h1803, 8'h04);
        repeat (20) @(posedge clk2x);
        if (trap) fail("trap stays up after a retry");
        if (start_ok || start_err || !start_busy) fail("a retry ended the request");
        fork begin wait (trap); end begin repeat (70000) @(posedge clk2x); fail("no trap after a retry"); end join_any disable fork;
        bus_rd(13'h1800, st); if (st[2:1] !== 2'b01) fail("STATUS after a retry does not show save");
        bus_wr(13'h1804, 8'h01); bus_wr(13'h1803, 8'h01);
        fork begin wait (start_ok); end begin repeat (2000) @(posedge clk2x); fail("no start_ok after a retry"); end join_any disable fork;

        // load
        apf_start(1);
        wait (trap);
        bus_rd(13'h1800, st); if (st[2:1] !== 2'b10) fail("STATUS does not show load");
        bus_wr(13'h1804, 8'h01);
        bus_wr(13'h1803, 8'h01);
        fork begin wait (load_ok); end begin repeat (2000) @(posedge clk2x); fail("no load_ok"); end join_any disable fork;

        // hidden-state taps
        bus_rd(13'h1808, st); if (st !== 8'h0C) fail("timer 0 flags");
        bus_rd(13'h1809, st); if (st !== 8'h05) fail("timer 1 flags");
        bus_rd(13'h180A, st); if (st !== 8'h0A) fail("timer 2 flags");
        bus_wr(13'h1805, 8'hA7); bus_wr(13'h1806, 8'hFD); bus_wr(13'h1807, 8'hD3);
        bus_wr(13'h1808, 8'hF9); bus_wr(13'h1809, 8'h06); bus_wr(13'h180A, 8'h03);
        if (set_go) fail("SET_GO before it was written");
        bus_wr(13'h180B, 8'h01);
        if (!set_go || set_irq !== 11'h5A7 || set_dma !== 7'h53 || set_timer !== 12'h369) fail("set values");
        bus_wr(13'h180B, 8'h00);
        if (set_go) fail("SET_GO stays on");
        for (integer k = 0; k < 8; k = k + 1) begin
            bus_rd(13'h1810 + k, st);
            if (st !== gpu_ref[8*k +: 8]) fail("GPU ranges byte");
        end

        // SPU tap
        bus_rd(13'h180C, st); if (st[0]) fail("SPU quiet before hold");
        bus_wr(13'h180C, 8'h01); repeat (12) @(posedge clk2x);
        if (!spu_hold) fail("SPU hold not set");
        bus_rd(13'h180C, st); if (!st[0]) fail("SPU quiet not read back");
        for (k = 0; k < 6; k = k + 1) begin : ss_rw
            reg [8:0] a; reg [31:0] d, got;
            a = $urandom; d = $urandom;
            bus_wr(13'h180D, a[7:0]); bus_wr(13'h180E, {7'd0, a[8]});
            // the port word goes through page word a
            for (i = 0; i < 4; i = i + 1) bus_wr(13'h1000 + 4 * a + i, d[8*i +: 8]);
            bus_wr(13'h180F, 8'h02);
            st = 1; while (st[0]) bus_rd(13'h180F, st);
            if (ss_regs[a] !== d) fail("SS write");
            ss_regs[a] = d ^ 32'hFFFF0000;
            for (i = 0; i < 4; i = i + 1) bus_wr(13'h1000 + 4 * a + i, 8'h3C);
            bus_wr(13'h180F, 8'h01);
            st = 1; while (st[0]) bus_rd(13'h180F, st);
            for (i = 0; i < 4; i = i + 1) begin bus_rd(13'h1000 + 4 * a + i, b); got[8*i +: 8] = b; end
            if (got !== (d ^ 32'hFFFF0000)) fail("SS read");
        end
        if (ss_strobes != 12) begin $display("SS strobes %0d", ss_strobes); fail("SS strobe count"); end
        pages[0] = 0; pages[1] = 255; pages[2] = 17;
        for (p = 0; p < 3; p = p + 1) begin
            for (i = 0; i < 2048; i = i + 1) begin pg[i] = $urandom; bus_wr(13'h1000 + i, pg[i]); end
            bus_wr(13'h1801, pages[p][7:0]);
            spu_noise = 1; repeat (50) @(posedge clk1x); spu_noise = 0; repeat (20) @(posedge clk1x);
            bus_wr(13'h1800, 8'h05);
            wait_copy;
            bad = 0;
            for (k = 0; k < 1024; k = k + 1) if (spu_ram[{pages[p][7:0], k[9:0]}] !== {pg[2*k+1], pg[2*k]}) bad = bad + 1;
            if (bad) begin $display("SPU page %0d: %0d halfwords wrong", pages[p], bad); fail("page to SPU RAM"); end
            for (k = 0; k < 1024; k = k + 1) spu_ram[{pages[p][7:0], k[9:0]}] = $urandom;
            spu_noise = 1; repeat (50) @(posedge clk1x); spu_noise = 0; repeat (20) @(posedge clk1x);
            bus_wr(13'h1800, 8'h07);
            wait_copy;
            bad = 0;
            for (i = 0; i < 2048; i = i + 1) begin
                bus_rd(13'h1000 + i, b);
                if (b !== spu_ram[{pages[p][7:0], i[10:1]}][8*i[0] +: 8]) bad = bad + 1;
            end
            if (bad) begin $display("SPU page %0d: %0d bytes wrong", pages[p], bad); fail("SPU RAM to page"); end
        end
        if (spurious < 10) fail("too few spurious SPU done pulses");
        bus_wr(13'h180C, 8'h00); repeat (12) @(posedge clk2x);
        if (spu_hold) fail("SPU hold stays on");

        card_on = 0; repeat (200) @(posedge clk2x);
        if (card_done_n < 50) fail("too little card traffic");
        $display("aux accesses %0d, card accesses %0d", aux_count, card_done_n);
        if (errors == 0) $display("PASS save exp1"); else $display("FAIL errors=%0d", errors);
        $finish;
    end
    initial begin #200_000_000; $display("FAIL timeout"); $finish; end
endmodule
`default_nettype wire
