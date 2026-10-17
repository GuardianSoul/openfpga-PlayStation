// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Bench for rtl/pocket/psx_memcard_slot.sv (run by scripts/run_memcard_slot.py).
//
// * APF model (clk_74a, 74.25 MHz): transaction-level copy of the sequence the
//   pinned APF bridge peripheral (MIRLO64 io_bridge_peripheral.v) produces:
//   writes = address, then data, then a one-clock bridge_wr, one transaction
//   every WPER clocks; reads = address, bridge_rd_data sampled RD_SAMPLE
//   clocks later (that sample is the answer to the PREVIOUS read: the bridge
//   buffers reads by one word), then a one-clock bridge_rd, one transaction
//   every RPER clocks. A final dummy read collects the last word. File bytes
//   travel big-endian on the wire; the bridge byte-swaps when LITTLE=1.
// * VRAM controller aux-port model (clk2x, 67.7376 MHz): aux_req must be held
//   with stable we/addr/din until a one-clock aux_done, which comes after a
//   random latency of LMIN..LMAX clocks; aux_dout is held until the next read
//   completes. reset_2x abandons an access without writing. Guest card writes
//   go straight into the model memory with a card_write pulse.
// * Checks: the PSRAM image when busy falls, no aux traffic while idle,
//   readback of all 131,072 bytes, overflow, and timings printed as JSON.
`timescale 1ps/1ps
`default_nettype none
module tb_memcard_slot;
    parameter LITTLE = 1;
    parameter FAULT = 0;

    localparam integer WORDS = 32768;
    localparam integer RD_SAMPLE = 5;           // clocks from address to sample

    // ---------------- plusargs ----------------
    integer seed0 = 1;
    reg [8*16-1:0] scen;     // load | reset_load | reset_fmt | overflow
    reg [8*16-1:0] image;    // real | mc_zero | nofile | zeros | ff | junk
    integer stall_pct, smin, smax, guest_rb, seed, lmin, lmax, wper, rper, phase_ps, rst_at, rst_len, guest_n;
    initial begin
        if (!$value$plusargs("SCEN=%s", scen)) scen = "load";
        if (!$value$plusargs("IMAGE=%s", image)) image = "real";
        if (!$value$plusargs("SEED=%d", seed)) seed = 1;
        if (!$value$plusargs("LMIN=%d", lmin)) lmin = 4;
        if (!$value$plusargs("LMAX=%d", lmax)) lmax = 30;
        if (!$value$plusargs("WPER=%d", wper)) wper = 88;
        if (!$value$plusargs("RPER=%d", rper)) rper = 88;
        if (!$value$plusargs("PHASE=%d", phase_ps)) phase_ps = 0;
        if (!$value$plusargs("RST_AT=%d", rst_at)) rst_at = 5000;
        if (!$value$plusargs("RST_LEN=%d", rst_len)) rst_len = 3;
        if (!$value$plusargs("GUEST=%d", guest_n)) guest_n = 300;
        if (!$value$plusargs("GUEST_RB=%d", guest_rb)) guest_rb = 97;
        if (!$value$plusargs("STALL_PCT=%d", stall_pct)) stall_pct = 0;
        if (!$value$plusargs("SMIN=%d", smin)) smin = 60;
        if (!$value$plusargs("SMAX=%d", smax)) smax = 140;
    end

    // ---------------- clocks ----------------
    reg clk_74a = 0, clk2x = 0;
    initial forever #6734 clk_74a = !clk_74a;                  // 74.25 MHz
    initial begin #(phase_ps + 1); forever #7382 clk2x = !clk2x; end  // 67.7376 MHz (14.763 ns)

    // ---------------- DUT ----------------
    reg [31:0] bridge_addr = 0, bridge_wr_data = 0;
    reg bridge_wr = 0, bridge_rd = 0, load_done = 0, reset_2x = 1, card_write = 0;
    wire [31:0] bridge_rd_data, aux_din, aux_dout;
    wire [20:0] aux_addr;
    wire overflow, busy, aux_req, aux_we, aux_done;
    psx_memcard_slot #(.BRIDGE_LITTLE(LITTLE), .FAULT(FAULT)) dut (
        .clk_74a(clk_74a), .bridge_addr(bridge_addr), .bridge_wr(bridge_wr),
        .bridge_wr_data(bridge_wr_data), .bridge_rd(bridge_rd), .bridge_rd_data(bridge_rd_data),
        .overflow(overflow), .clk2x(clk2x), .reset_2x(reset_2x), .busy(busy),
        .aux_req(aux_req), .aux_we(aux_we), .aux_addr(aux_addr), .aux_din(aux_din),
        .aux_dout(aux_dout), .aux_done(aux_done), .card_write(card_write), .load_done(load_done));

    // Reference blank card (our psx_memcard_blank.sv, checked against psx-spx
    // by scripts/run_memcard_blank.py), independent of the DUT's table.
    reg [13:0] blank_index = 14'h3FFF;   // first use must be a change
    wire [63:0] blank_word64;
    psx_memcard_blank blank_ref(.word_index(blank_index), .word(blank_word64));

    integer errors = 0;
    task fail(input [8*64-1:0] msg); begin
        errors = errors + 1;
        if (errors < 12) $display("ERROR t=%0t %0s", $time, msg);
    end endtask

    // ---------------- aux model ----------------
    reg [31:0] mem [0:WORDS-1];
    reg [31:0] expect_mem [0:WORDS-1];
    reg [31:0] file_word [0:WORDS-1];       // little-endian card word i from the file
    reg [31:0] dout_r = 32'hDEAD_BEEF;
    reg done_r = 0;
    reg [1:0] mstate = 0;
    reg m_we; reg [20:0] m_addr; reg [31:0] m_din;
    integer mcnt = 0, aux_writes_idle = 0, aux_cycles_watch = 0, aux_total = 0;
    reg watch_idle = 0;
    reg [7:0] visits [0:WORDS-1];
    reg [31:0] h;
    initial for (h = 0; h < WORDS; h = h + 1) visits[h] = 0;
    function [31:0] lat_hash(input [14:0] a, input we, input [7:0] n);
        reg [31:0] x;
        begin
            x = seed0 * 32'h9E37_79B1 ^ {a, we, n, 8'h5B} * 32'h85EB_CA6B;
            x = x ^ (x >> 15); x = x * 32'h2C1B_3C6D; x = x ^ (x >> 12);
            x = x * 32'h297A_2D39; lat_hash = x ^ (x >> 15);
        end
    endfunction
    realtime acc_start = 0, last_wr_done = 0; real last_wr_aux_ns = 0;
    assign aux_dout = dout_r;
    assign aux_done = done_r;
    always @(posedge clk2x) begin
        if (reset_2x) begin
            mstate <= 0; done_r <= 0;
        end else case (mstate)
            0: begin
                done_r <= 0;
                if (aux_req) begin
                    m_we <= aux_we; m_addr <= aux_addr; m_din <= aux_din;
                    // Latency is a hash of (seed, address, we, pass over that word),
                    // not a shared random stream, so a DUT that makes extra or fewer
                    // accesses elsewhere sees the same latency for the same access
                    // (fair black-box comparison).
                    h = lat_hash(aux_addr[14:0], aux_we, visits[aux_addr[14:0]]);
                    visits[aux_addr[14:0]] = visits[aux_addr[14:0]] + 1;
                    if (stall_pct != 0 && h[31:16] % 100 < stall_pct)     // occasional long stall
                        mcnt <= smin + h[15:0] % (smax - smin + 1);
                    else mcnt <= lmin + (lmax > lmin ? h[15:0] % (lmax - lmin + 1) : 0);
                    mstate <= 1; aux_total = aux_total + 1;
                    if (aux_addr[20:15] != 0) fail("aux address outside the card window");
                    if (aux_we && !busy) aux_writes_idle = aux_writes_idle + 1;
                    acc_start = $realtime;
                end
            end
            1: begin
                if (!aux_req || aux_we !== m_we || aux_addr !== m_addr || aux_din !== m_din)
                    fail("aux request changed or dropped before aux_done");
                if (mcnt <= 1) begin
                    if (m_we) mem[m_addr[14:0]] <= m_din; else dout_r <= mem[m_addr[14:0]];
                    if (m_we && busy && m_addr == WORDS - 1) begin   // the last load write
                        last_wr_aux_ns = ($realtime - acc_start) / 1000.0 + 14.763;  // req sampled .. done visible
                        last_wr_done = $realtime;
                    end
                    done_r <= 1; mstate <= 2;
                end
                mcnt <= mcnt - 1;
            end
            2: begin done_r <= 0; mstate <= 0; end
        endcase
        if (watch_idle && aux_req) aux_cycles_watch = aux_cycles_watch + 1;
    end

    // ---------------- measurements ----------------
    realtime t_last_wr = 0, t_busy_fall = 0, t_fmt_rise = 0, t_fmt_fall = 0;
    integer rd_lat_max = 0, rd_lat_cnt = 0, rd_lat_now = 0, rd_count = 0;
    reg rd_pending = 0, prev_busy = 1, prev_fmt = 0;
    integer busy_falls = 0;
    always @(posedge clk_74a) begin
        if (dut.rd_strobe && !dut.rst_74 && dut.req_accept) begin rd_pending <= 1; rd_lat_cnt <= 1; end
        else if (rd_pending) begin
            rd_lat_cnt <= rd_lat_cnt + 1;
            if (dut.rdv_edge) begin
                rd_pending <= 0;
                // bridge_rd_data is loaded rd_lat_cnt edges after the edge that saw bridge_rd.
                if (rd_lat_cnt > rd_lat_max) rd_lat_max = rd_lat_cnt;
                rd_count = rd_count + 1;
            end
        end
    end
    always @(posedge clk2x) begin
        prev_busy <= busy; prev_fmt <= dut.fmt_active;
        if (prev_busy && !busy) begin t_busy_fall = $realtime; busy_falls = busy_falls + 1; end
        if (!prev_fmt && dut.fmt_active) t_fmt_rise = $realtime;
        if (prev_fmt && !dut.fmt_active) t_fmt_fall = $realtime;
    end

    // ---------------- APF model ----------------
    function [31:0] swap32(input [31:0] w); swap32 = {w[7:0], w[15:8], w[23:16], w[31:24]}; endfunction
    // card word (little-endian bytes) -> wire word (file bytes big-endian) -> bridge word
    function [31:0] to_bridge(input [31:0] card); begin
        to_bridge = LITTLE ? swap32(swap32(card)) : swap32(card);
    end endfunction
    function [31:0] from_bridge(input [31:0] b); begin
        from_bridge = LITTLE ? b : swap32(b);    // back to little-endian card bytes
    end endfunction

    reg done_after = 0;
    task apf_write(input [31:0] addr, input [31:0] data); begin
        @(posedge clk_74a); bridge_addr <= addr;
        repeat (wper > 40 ? 30 : wper / 2) @(posedge clk_74a);
        bridge_wr_data <= data;
        @(posedge clk_74a); bridge_wr <= 1;
        if (addr[31:17] == 15'h1000) t_last_wr = $realtime;
        @(posedge clk_74a); bridge_wr <= 0;
        if (done_after) load_done <= 1;    // allcomplete right after the last slot write
        repeat ((wper > 40 ? wper - 33 : wper - wper / 2 - 3)) @(posedge clk_74a);
    end endtask

    reg [31:0] rd_sampled;
    task apf_read(input [31:0] addr); begin
        @(posedge clk_74a); bridge_addr <= addr;
        repeat (RD_SAMPLE - 1) @(posedge clk_74a);
        @(posedge clk_74a); rd_sampled = bridge_rd_data;   // answer to the previous read
        bridge_rd <= 1;
        @(posedge clk_74a); bridge_rd <= 0;
        repeat (rper - RD_SAMPLE - 2) @(posedge clk_74a);
    end endtask

    // ---------------- images ----------------
    function [7:0] rbyte(input integer dummy); rbyte = $urandom; endfunction
    reg has_file;
    integer i, k, mism, first_bad;
    reg [31:0] w, v;
    task build_image; begin
        has_file = image != "nofile";
        for (i = 0; i < WORDS; i = i + 1) begin
            mem[i] = $urandom;                      // PSRAM power-up junk
            if (image == "zeros" || image == "mc_zero") w = 0;
            else if (image == "ff" || image == "junk") w = 32'hFFFF_FFFF;
            else w = $urandom;
            file_word[i] = w;
        end
        if (image == "junk") begin                  // 0.5.0-style: game bytes in frame 63 and block 1
            for (i = 63 * 32; i < 64 * 32; i = i + 1) file_word[i] = $urandom;
            for (i = 0; i < 3; i = i + 1) file_word[64 * 32 + i] = $urandom;
        end
        if (image == "real" || image == "mc_zero") file_word[0] = {file_word[0][31:16], 8'h43, 8'h4D}; // bytes 'M','C'
        else if (file_word[0][15:0] == 16'h434D) file_word[0][0] = !file_word[0][0];
        if (!has_file && mem[0][15:0] == 16'h434D) mem[0][0] = !mem[0][0];
        // Byte-order trap for "real": 'C','M' (swapped magic) must not count as magic.
        for (i = 0; i < WORDS; i = i + 1) expect_mem[i] = has_file ? file_word[i] : mem[i];
        if (expect_mem[0][15:0] != 16'h434D) begin
            for (i = 0; i < 2048; i = i + 1) begin
                blank_index = i >> 1; #1;
                expect_mem[i] = i[0] ? blank_word64[63:32] : blank_word64[31:0];
            end
        end
    end endtask

    task load_all(input integer upto); begin
        for (i = 0; i < upto; i = i + 1) begin
            done_after = upto == WORDS && i == WORDS - 1;
            apf_write(32'h2000_0000 + 4 * i, to_bridge(file_word[i]));
            // other slots on the bridge must not reach the card
            if (i % 4096 == 7) apf_write(32'h0000_0000 + 4 * i, 32'h1234_5678);
            if (i % 4096 == 9) apf_write(32'h2002_0000 + 4 * i, 32'h8765_4321);
        end
    end endtask

    task compare_mem(input [8*24-1:0] what); begin
        mism = 0; first_bad = -1;
        for (k = 0; k < WORDS; k = k + 1) if (mem[k] !== expect_mem[k]) begin
            if (first_bad < 0) first_bad = k; mism = mism + 1;
        end
        if (mism) begin
            $display("ERROR %0s: %0d PSRAM words differ, first word %0d: got %08x want %08x",
                what, mism, first_bad, mem[first_bad], expect_mem[first_bad]);
            errors = errors + 1;
        end
    end endtask

    integer guest_rb_writes = 0;
    task guest_write_later(input integer addr, input integer delay74);
        reg [31:0] gv;
        begin
            // starts right after the APF read of addr-1 was issued; lands well
            // before the APF read of addr (RPER >= 88)
            repeat (RD_SAMPLE + 2 + delay74) @(posedge clk_74a);
            @(posedge clk2x);
            gv = $urandom; mem[addr] = gv; expect_mem[addr] = gv; card_write <= 1;
            guest_rb_writes = guest_rb_writes + 1;
            @(posedge clk2x); card_write <= 0;
        end
    endtask
    integer rb_bad, rb_first, g, ga;
    reg [31:0] got;
    initial begin
        #1;
        seed0 = seed; void'($urandom(seed));
        build_image;
        // byte order evidence: card byte 0 of a "real" card is 'M' at bits [7:0]
        // Wire word = file bytes 0..3 big-endian (byte 0 in [31:24]).
        if (image == "real" && (swap32(file_word[0]) >> 16) != 32'h4D43) fail("bench byte order setup");
        repeat (20) @(posedge clk2x); reset_2x <= 0;
        wait (!dut.rst_2x);
        if (!busy) fail("busy low before the load");

        if (scen == "reset_load") begin
            if (has_file) load_all(rst_at);
            @(posedge clk2x); reset_2x <= 1;
            repeat (rst_len) @(posedge clk2x); reset_2x <= 0;
            // APF writes during/after the reset handshake are lost; it reloads the slot
            if (has_file) for (i = rst_at; i < rst_at + 3; i = i + 1)
                apf_write(32'h2000_0000 + 4 * i, 32'h0BAD_0BAD);
            wait (!dut.rst_2x);
            if (!busy) fail("busy low after reset");
        end
        if (scen == "overflow") begin
            load_all(64);
            repeat (2000) @(posedge clk_74a);
            if (!overflow) fail("overflow flag not set by writes faster than aux");
            $display("RESULT {\"scen\":\"overflow\",\"overflow\":%0d,\"errors\":%0d}", overflow, errors);
            if (errors == 0) $display("PASS"); else $display("FAIL");
            $finish;
        end
        if (has_file) load_all(WORDS);
        @(posedge clk_74a); load_done <= 1;

        if (scen == "reset_fmt") begin
            wait (dut.fmt_active && dut.fmt_idx > rst_at);
            @(posedge clk2x); reset_2x <= 1;
            repeat (rst_len) @(posedge clk2x); reset_2x <= 0;
            wait (!dut.rst_2x);
        end

        // busy must fall exactly once, with the final image already in PSRAM
        wait (!busy && !dut.rst_2x);
        @(posedge clk2x); #1;
        compare_mem("image when busy fell");
        if (overflow) fail("overflow during load");

        // the slot must stay off the aux port while idle (guest bandwidth)
        watch_idle = 1;
        repeat (3000) @(posedge clk2x);
        // guest card writes through the core side, with card_write pulses
        for (g = 0; g < guest_n; g = g + 1) begin
            @(posedge clk2x);
            ga = (g < 4) ? (g == 0 ? 0 : (g == 1 ? WORDS - 1 : $urandom % 2048)) : $urandom % WORDS;
            v = $urandom; mem[ga] = v; expect_mem[ga] = v; card_write <= 1;
            @(posedge clk2x); card_write <= 0;
        end
        repeat (50) @(posedge clk2x);
        watch_idle = 0;
        if (aux_cycles_watch != 0) fail("aux_req while idle");
        if (aux_writes_idle != 0) fail("aux write after busy fell");

        // APF save readback: every word, then a dummy read for the last one
        rb_bad = 0; rb_first = -1;
        for (i = 0; i <= WORDS; i = i + 1) begin
            // A guest write to the word APF reads next,
            // landing at a random time before that read: the readback must
            // return the new value.
            if (i < WORDS - 1 && guest_rb != 0 && i % guest_rb == 5) begin
                fork
                    guest_write_later(i + 1, $urandom % 70);
                join_none
            end
            apf_read(32'h2000_0000 + 4 * (i < WORDS ? i : WORDS - 1));
            if (i > 0) begin
                got = from_bridge(rd_sampled);
                if (got !== expect_mem[i - 1]) begin
                    if (rb_first < 0) rb_first = i - 1; rb_bad = rb_bad + 1;
                end
            end
        end
        if (rb_bad) begin
            $display("ERROR readback: %0d of %0d words wrong, first %0d", rb_bad, WORDS, rb_first);
            errors = errors + 1;
        end
        if (overflow) fail("overflow during readback");
        if (busy_falls != 1) fail("busy fell more than once");
        if (aux_writes_idle != 0) fail("aux write after busy fell");

        $display("RESULT {\"scen\":\"%0s\",\"image\":\"%0s\",\"little\":%0d,\"fault\":%0d,\"seed\":%0d,\"lmin\":%0d,\"lmax\":%0d,\"stall_pct\":%0d,\"smax\":%0d,\"wper\":%0d,\"rper\":%0d,\"phase_ps\":%0d,\"last_write_to_busy_ns\":%0.1f,\"last_write_aux_latency_ns\":%0.1f,\"last_write_done_to_busy_ns\":%0.1f,\"format_ns\":%0.1f,\"formatted\":%0d,\"read_strobe_to_data_max_clk74\":%0d,\"read_deadline_clk74\":%0d,\"read_slack_clk74\":%0d,\"reads\":%0d,\"readback_words_wrong\":%0d,\"idle_aux_cycles\":%0d,\"guest_writes_during_readback\":%0d,\"aux_accesses\":%0d,\"overflow\":%0d,\"errors\":%0d}",
            scen, image, LITTLE, FAULT, seed0, lmin, lmax, stall_pct, smax, wper, rper, phase_ps,
            has_file ? (t_busy_fall - t_last_wr) / 1000.0 : -1.0, has_file ? last_wr_aux_ns : -1.0,
            has_file ? (t_busy_fall - last_wr_done) / 1000.0 : -1.0,
            t_fmt_fall > t_fmt_rise ? (t_fmt_fall - t_fmt_rise) / 1000.0 : 0.0,
            t_fmt_fall > 0, rd_lat_max, rper - 2, rper - 2 - rd_lat_max, rd_count, rb_bad,
            aux_cycles_watch, guest_rb_writes, aux_total, overflow, errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end

    initial begin #400_000_000_000; $display("ERROR timeout"); $display("FAIL"); $finish; end
endmodule
`default_nettype wire
