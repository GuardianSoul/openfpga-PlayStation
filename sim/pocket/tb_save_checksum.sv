// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// save-checksum (scripts/prepare_save_checksum.py): the save device with cs and the overflow port
// (dut) next to the device before the step (old; scripts/run_save_checksum.py renames its module to
// psx_save_exp1_old). Both get the same inputs on every clock. Checks:
//   - lockstep: every old output is equal on every clk2x edge; the read data too, except at
//     0x1830-0x1833 (old reads 0 there)
//   - reference: cs equals a model built from the OLD device's copy engine (old.own_req, old.cp_dir,
//     old.cp_q, aux_dout) on every edge: rotate-left-and-add per copy word, cleared by a write edge
//     to 0x1830-0x1833 (the clear wins); the read of 0x1830 + k is byte k of it, bit 31 flipped
//     while old.want_load and the overflow input (two clk2x stages late) are set; overflow_clear
//     pulses exactly one clk2x after each FINISH write edge without the retry bit
//   - directed, with a save buffer model on the aux port and a model of the slot's overflow (set by
//     the bench, cleared a few clk_74a after the pulse, as the slot's toggle crossing does):
//       save four pages, load them back: the two sums are equal and not 0
//       one buffer word stale (a dropped upload write), two words swapped, one bit flipped: the
//       load's sum differs from the save's in each case
//       card accesses on the aux port between the copy words do not change the sum
//       a clear on the clock of a copy word's done: 0 afterwards
//       overflow set during a load request: bit 31 reads flipped until FINISH; the FINISH pulse
//       clears the model's flag; set during a save request: no flip; a retry write: no pulse
//   - random bus, APF, aux, SPU, overflow and reset traffic: lockstep and reference on every edge
`timescale 1ns/1ps
`default_nettype none
module tb_save_checksum;
    reg clk2x = 0, clk1x = 0, clk_74a = 0;
    always #7.38 clk2x = !clk2x;
    always @(posedge clk2x) clk1x <= !clk1x;
    always #6.73 clk_74a = !clk_74a;

    // shared inputs
    reg reset_2x = 1, core_reset = 0;
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
    reg [8:0] trackinfo_addr = 0; reg [19:0] trackinfo_data = 0; reg trackinfo_write = 0;
    reg ovf = 0;                       // the slot's overflow (clk_74a)
    wire dut_oclr;

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
        .trackinfo_write(trackinfo_write), .trackinfo_addr(trackinfo_addr), .trackinfo_data(trackinfo_data), \
        .aux_req(P``_aux_req), .aux_we(P``_aux_we), .aux_addr(P``_aux_addr), .aux_din(P``_aux_din), .aux_dout(aux_dout), .aux_done(aux_done), \
        .up_req(up_req), .up_we(up_we), .up_addr(up_addr), .up_din(up_din), .up_dout(P``_up_dout), .up_done(P``_up_done), \
        .clk_74a(clk_74a), .savestate_supported(P``_sup), .savestate_addr(P``_ss_addr), .savestate_size(P``_ss_size), \
        .savestate_maxloadsize(P``_ss_max), \
        .savestate_start(savestate_start), .savestate_start_ack(P``_s_ack), .savestate_start_busy(P``_s_busy), \
        .savestate_start_ok(P``_s_ok), .savestate_start_err(P``_s_err), \
        .savestate_load(savestate_load), .savestate_load_ack(P``_l_ack), .savestate_load_busy(P``_l_busy), \
        .savestate_load_ok(P``_l_ok), .savestate_load_err(P``_l_err), \
        .disc(disc), .disc_set(P``_disc_set), .disc_set_tgl(P``_disc_tgl), .disc_silent(P``_disc_silent), \
        .disc_settled(disc_settled), .disc_wrong(disc_wrong), .core_held(core_held), .cd_state(cd_state)
`define ALL(P) \
    wire [293:0] P``_all = {P``_trap, P``_save_mode, P``_cd_freeze, P``_set_sel, P``_set_data, \
        P``_spu_hold, P``_ss_rden, P``_ss_wren, P``_spuram_req, P``_spuram_rnw, P``_mdec_tap, \
        P``_ss_adr, P``_ss_wdata, P``_spuram_adr, P``_spuram_wdata, \
        P``_aux_req, P``_aux_we, P``_up_done, P``_aux_addr, P``_aux_din, P``_up_dout, \
        P``_sup, P``_s_ack, P``_s_busy, P``_s_ok, P``_s_err, P``_l_ack, P``_l_busy, P``_l_ok, P``_l_err, \
        P``_ss_addr, P``_ss_size, P``_ss_max, P``_disc_set, P``_disc_tgl, P``_disc_silent};

    `DEVICE(psx_save_exp1_old, old, (.ROM_HEX("tb_save_exp1_rom40.hex"), .EXT_HEX("tb_save_rom_ext.hex"))) );
    `ALL(old)
    `DEVICE(psx_save_exp1, dut, (.ROM_HEX("tb_save_exp1_rom40.hex"), .EXT_HEX("tb_save_rom_ext.hex"))),
        .overflow(ovf), .overflow_clear(dut_oclr));
    `ALL(dut)

    integer errors = 0;
    task fail(input [8*90-1:0] msg); begin
        errors = errors + 1;
        if (errors < 15) $display("ERROR t=%0t %0s (addr %h old %h new %h cs %h ref %h)", $time, msg, bus_addr, old_q, dut_q, dut.cs, cs_ref);
    end endtask

    // ---------------- the checker ----------------
    reg [31:0] cs_ref = 0; reg [1:0] ovf_ref = 0; reg wr_d = 0, fin_d = 0;
    reg [31:0] w_ref = 0; reg v_ref = 0;
    integer edges = 0, cs_reads = 0, sums = 0, clears = 0, flips = 0, pulses = 0, both_valid = 0;
    wire at_cs = bus_addr[12:6] == 7'b1100000 && bus_addr[5:2] == 4'b1100;
    wire [31:0] rd_ref = {cs_ref[31] ^ (old.want_load && ovf_ref[1]), cs_ref[30:0]};
    always @(posedge clk2x) begin
        // (values before this edge)
        edges = edges + 1;
        if (old_all !== dut_all) fail("an output of the old device differs");
        if (dut.cs !== cs_ref) fail("cs differs from the reference");
        if (dut_oclr !== fin_d) fail("overflow_clear is not one pulse after a FINISH write");
        if (dut_oclr) pulses = pulses + 1;
        if (at_cs) begin
            cs_reads = cs_reads + 1;
            if (dut_q !== rd_ref[8*bus_addr[1:0] +: 8]) fail("0x1830-0x1833 read is not the reference byte");
            if (old_q !== 8'd0) fail("old device: 0x1830-0x1833 not 0");
            if (old.want_load && ovf_ref[1] && bus_addr[1:0] == 2'd3) flips = flips + 1;
        end else begin
            if (old_q !== dut_q) fail("read data differs outside 0x1830-0x1833");
            if (old_q === dut_q && ^dut_q !== 1'bx) both_valid = both_valid + 1;
        end
        // the reference, in the device's order: the sum, then the clear (the clear wins)
        if (!reset_2x) begin
            // (the new device adds a word one clk2x after its done, from a register; a clear on the
            // done's own clock drops it, as the old one-clock sum did)
            if (v_ref) begin cs_ref = {cs_ref[30:0], cs_ref[31]} + w_ref; sums = sums + 1; end
            if (bus_write && !wr_d && at_cs) begin cs_ref = 0; clears = clears + 1; end
        end
        w_ref = old.cp_dir ? aux_dout : old.cp_q;
        v_ref = old.own_req && aux_done && !reset_2x && !(bus_write && !wr_d && at_cs);
        fin_d = bus_write && !wr_d && bus_addr == 13'h1803 && !bus_dataWrite[2];   // (no reset term in the device either)
        ovf_ref = {ovf_ref[0], ovf};
        wr_d = bus_write;
    end

    // ---------------- models ----------------
    // the save buffer behind the aux port (bit 20 set: 13 bits of {page_lo[3:0], word}); card words apart
    reg [31:0] bufmem [0:8191];
    reg [31:0] cardmem [0:1023];
    reg noise = 0; reg [2:0] aux_cnt = 0;
    always @(posedge clk2x) if (!noise) begin
        aux_done <= 1'b0;
        if (old_aux_req && !aux_done) begin
            aux_cnt <= aux_cnt + 1'b1;
            if (aux_cnt == 3'd4) begin
                aux_done <= 1'b1; aux_cnt <= 0;
                if (old_aux_addr[20]) begin
                    if (old_aux_we) bufmem[old_aux_addr[12:0]] <= old_aux_din; else aux_dout <= bufmem[old_aux_addr[12:0]];
                end else begin
                    if (old_aux_we) cardmem[old_aux_addr[9:0]] <= old_aux_din; else aux_dout <= cardmem[old_aux_addr[9:0]];
                end
            end
        end else aux_cnt <= 0;
    end
    // the slot's overflow: the pulse crosses as a toggle (two clk_74a stages) and clears the flag
    reg clr_tgl = 0; reg [2:0] clr_s = 0; reg ovf_set = 0;
    always @(posedge clk2x) if (dut_oclr) clr_tgl <= !clr_tgl;
    always @(posedge clk_74a) begin
        clr_s <= {clr_s[1:0], clr_tgl};
        if (!noise) begin
            if (clr_s[2] != clr_s[1]) ovf <= 1'b0;
            if (ovf_set) ovf <= 1'b1;
        end
    end
    // card accesses on the aux port (the slot's clk2x side), one after another while card_on
    reg card_on = 0; integer card_n = 0;
    always @(posedge clk2x) if (!noise) begin
        if (up_req && old_up_done) begin up_req <= 0; card_n = card_n + 1; end
        else if (card_on && !up_req && ($urandom % 3 == 0)) begin
            up_req <= 1; up_we <= $urandom % 2; up_addr <= $urandom % 1024; up_din <= $urandom;
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
    reg [7:0] o, d;
    task cs_read(output [31:0] v); integer k; begin
        for (k = 0; k < 4; k = k + 1) begin bus_rd(13'h1830 + k, o, d); v[8*k +: 8] = d; end
    end endtask
    // one page copy: dir 0 staging page -> buffer page, 1 buffer page -> staging page
    task copy(input [7:0] page, input dir); begin
        bus_wr(13'h1801, page); bus_wr(13'h1802, 8'd0); bus_wr(13'h1800, dir ? 8'h03 : 8'h01);
        bus_rd(13'h1800, o, d); while (d[0]) bus_rd(13'h1800, o, d);
    end endtask
    task fill(input integer s); integer n; begin
        for (n = 0; n < 2048; n = n + 1) bus_wr(13'h1000 + n, (n * 37 + s * 101) ^ (n >> 5) ^ s);
    end endtask
    // "save": four pages written; "load": the same four pages read back, in order
    task save4(output [31:0] v); integer p; begin
        bus_wr(13'h1830, 8'h00);
        for (p = 0; p < 4; p = p + 1) begin fill(p); copy(p, 0); end
        cs_read(v);
    end endtask
    task load4(output [31:0] v); integer p; begin
        bus_wr(13'h1831, 8'h5A);           // any byte of the register, any value: a clear
        for (p = 0; p < 4; p = p + 1) copy(p, 1);
        cs_read(v);
    end endtask

    // ---------------- random traffic ----------------
    task traffic(input integer cycles); reg [12:0] a; integer k, hold; begin
        k = 0;
        while (k < cycles) begin
            @(posedge clk1x);
            case ($urandom % 12)
                0, 1:    a = 13'h1800 + ($urandom % 64);
                2, 3, 4: a = 13'h1830 + ($urandom % 4);
                5:       a = 13'h1820 + ($urandom % 32);
                6:       a = 13'h1803;
                7:       a = 13'h1000 + ($urandom % 2048);
                8:       a = 13'h1800;
                default: a = $urandom;
            endcase
            bus_addr <= a; bus_dataWrite <= (a == 13'h1800) ? ($urandom % 2 ? 8'h01 : 8'h03) : $urandom; bus_write <= 0;
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
        if ($urandom % 200 == 0) ovf <= $urandom;
    end

    reg [31:0] s_sum, l_sum, v, w;
    integer n, same_clock = 0, k;
    initial begin
        for (n = 0; n < 8192; n = n + 1) bufmem[n] = $urandom;
        for (n = 0; n < 1024; n = n + 1) cardmem[n] = $urandom;
        repeat (8) @(posedge clk2x); reset_2x <= 0; repeat (4) @(posedge clk1x);
        // a save and its load: equal sums
        save4(s_sum); load4(l_sum);
        if (s_sum !== l_sum) fail("load sum differs from the save sum");
        if (s_sum == 0) fail("save sum 0");
        // with card accesses between the copy words: the same
        card_on = 1; load4(v); card_on = 0;
        if (v !== s_sum) fail("card accesses changed the sum");
        if (card_n < 100) fail("too few card accesses during the copies");
        // a stale word (an upload write the slot dropped): the buffer word keeps an older value
        w = bufmem[13'h0400 + 100]; bufmem[13'h0400 + 100] = w ^ 32'h0001_0000 ^ 32'h8000_0000;
        load4(v); if (v === s_sum) fail("a stale word was not seen");
        bufmem[13'h0400 + 100] = w; load4(v); if (v !== s_sum) fail("sum after the repair");
        // a single bit
        bufmem[13'h0600 + 511] = bufmem[13'h0600 + 511] ^ 32'h0000_0001; load4(v); if (v === s_sum) fail("a flipped bit was not seen");
        bufmem[13'h0600 + 511] = bufmem[13'h0600 + 511] ^ 32'h0000_0001;
        // two words swapped
        w = bufmem[13'h0200 + 7]; bufmem[13'h0200 + 7] = bufmem[13'h0200 + 8]; bufmem[13'h0200 + 8] = w;
        if (bufmem[13'h0200 + 7] == bufmem[13'h0200 + 8]) fail("bench: swapped words are equal");
        load4(v); if (v === s_sum) fail("two swapped words were not seen");
        w = bufmem[13'h0200 + 7]; bufmem[13'h0200 + 7] = bufmem[13'h0200 + 8]; bufmem[13'h0200 + 8] = w;
        // a clear on the clock of a copy word's done
        for (n = 0; n < 30; n = n + 1) begin
            bus_wr(13'h1801, n % 4); bus_wr(13'h1800, 8'h03);
            repeat (n + 3) @(posedge clk2x);
            // (values settled after an edge: aux_done high now means the next edge is a copy word's done)
            @(posedge clk2x); #1;
            while (!(aux_done && old.own_req)) begin @(posedge clk2x); #1; end
            bus_addr = 13'h1832; bus_dataWrite = 8'h00; bus_write = 1;   // the next edge sees the write edge too
            @(posedge clk2x); #1;
            same_clock = same_clock + 1;
            if (dut.cs !== 32'd0) fail("a copy word won over the clear");
            bus_write = 0;
            bus_rd(13'h1800, o, d); while (d[0]) bus_rd(13'h1800, o, d);
        end
        if (same_clock < 10) fail("too few same-clock sum and clear cases");
        // overflow: a load request is open
        @(posedge clk_74a); savestate_load <= 1; repeat (4) @(posedge clk_74a); savestate_load <= 0;
        repeat (20) @(posedge clk2x);
        if (!old.want_load) fail("bench: no load request");
        save4(s_sum); cs_read(v); if (v !== s_sum) fail("sum changed");
        @(posedge clk_74a); ovf_set <= 1; @(posedge clk_74a); ovf_set <= 0;
        repeat (6) @(posedge clk2x);
        cs_read(v); if (v !== (s_sum ^ 32'h8000_0000)) fail("overflow during a load: bit 31 not flipped");
        bus_wr(13'h1803, 8'h04);   // a retry: no clear
        repeat (20) @(posedge clk_74a);
        if (!ovf) fail("a retry write cleared the overflow");
        // FINISH: the pulse clears the flag (and the load request ends)
        bus_wr(13'h1803, 8'h02);
        repeat (20) @(posedge clk_74a);
        if (ovf) fail("FINISH did not clear the overflow");
        cs_read(v); if (v !== s_sum) fail("sum after FINISH");
        // a save request: the flag does not show
        @(posedge clk_74a); savestate_start <= 1; repeat (4) @(posedge clk_74a); savestate_start <= 0;
        repeat (20) @(posedge clk2x);
        if (!old.want_save) fail("bench: no save request");
        @(posedge clk_74a); ovf_set <= 1; @(posedge clk_74a); ovf_set <= 0;
        repeat (6) @(posedge clk2x);
        cs_read(v); if (v !== s_sum) fail("overflow during a save: the read changed");
        bus_wr(13'h1803, 8'h01);
        repeat (20) @(posedge clk_74a);
        if (ovf) fail("FINISH of a save did not clear the overflow");
        // the clear decode: writes next to the register do not clear
        save4(s_sum);
        for (k = 4; k < 16; k = k + 1) bus_wr(13'h1830 + k, 8'h00);   // 0x1834-0x183F
        bus_wr(13'h1810, 8'h00); bus_wr(13'h1828, 8'h00);
        cs_read(v); if (v !== s_sum) fail("a write next to 0x1830 cleared the sum");

        noise = 1;
        traffic(150000);
        noise = 0;
        @(posedge clk2x); reset_2x <= 0; core_reset <= 0; up_req <= 0; ovf <= 0;
        repeat (20) @(posedge clk1x);

        if (sums < 20000 || clears < 500 || flips < 50 || pulses < 100) fail("too few sums, clears, flipped reads or pulses");
        if (both_valid < 100000 || cs_reads < 10000) fail("too few valid lockstep reads");
        $display("CHECKSUM edges=%0d sums=%0d clears=%0d pulses=%0d cs_reads=%0d flipped_reads=%0d same_clock=%0d card_accesses=%0d valid_lockstep_reads=%0d save_sum=%h",
                 edges, sums, clears, pulses, cs_reads, flips, same_clock, card_n, both_valid, s_sum);
        if (errors == 0) $display("PASS save checksum");
        else $display("FAIL save checksum: %0d errors", errors);
        $finish;
    end
endmodule
`default_nettype wire
