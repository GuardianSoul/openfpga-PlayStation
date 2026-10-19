// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Runs psx_sdram_ctrl and, for comparison, the reference sdram.sv (when
// compiled with -DWITH_REFERENCE) through the same random PSX-like traffic,
// each against its own sdram_chip_model:
//   ch1: CPU scalar reads, I-cache fills and back-to-back DMA reads
//   ch2: CPU writes with random byte enables (posted)
//   ch3: BIOS-loader words and SPU halfword reads/writes
//   dmafifo: DMA write runs, mostly within one row
//   refreshForce pulses
// Regions are disjoint per requester (banks 0-1 CPU, 2 SPU, 3 DMA writes), so
// every read is checked against a golden copy at completion, and the chip
// contents are compared with the golden copy at the end.
// Each bench logs one line per access (kind, issue and completion clk1x
// cycle) to <prefix>.log; run_sdram_ctrl.py compares the two logs.
`timescale 1ns/1ps
`default_nettype none
module sdram_bench #(parameter USE_REF = 0, parameter PREFIX = "ours", parameter integer SEED = 1,
                     parameter integer OPS = 4000, parameter integer CAS = 2, parameter integer SPU_FIRST = 0, parameter integer OPEN_ROW = 0,
                     // ns: forwarded chip-clock delay, chip clock-to-data max / hold min, data return delay
                     parameter real DCLK = 2.0, TAC = 6.0, TOH = 2.5, TRET = 2.0) (
    input wire clk3, clk1,
    output reg done = 0,
    output integer errors
);
    // ------------------------------------------------------------ DUT
    wire [15:0] dq; wire [12:0] a; wire [1:0] ba; wire dqml, dqmh, ncs, nwe, nras, ncas, cke, chip_clk;
    reg init = 1, refresh_force = 0;
    reg [26:0] ch1_addr = 0, ch2_addr = 0, ch3_addr = 0;
    reg ch1_req = 0, ch1_dma = 0, ch1_cache = 0, ch2_req = 0, ch3_req = 0, ch3_rnw = 0;
    reg [1:0] ch1_cnt = 0;
    reg [31:0] ch2_din = 0, ch3_din = 0;
    reg [3:0] ch2_be = 0, ch3_be = 0;
    wire [31:0] ch1_dout32, ch3_dout, dma_data, cache_data, ch2_dout;
    wire [127:0] ch1_dout;
    wire ch1_ready, ch2_ready, ch3_ready, dma_wr, dma_rp, ram_idle;
    wire [3:0] cache_wr; wire [7:0] cache_addr;
    // dmafifo (fall-through, clk3x)
    reg [26:0] fifo_adr [0:15]; reg [31:0] fifo_dat [0:15];
    reg [4:0] fifo_w = 0, fifo_r = 0;
    wire fifo_empty = fifo_w == fifo_r;
    wire fifo_read;

    generate if (USE_REF) begin : ref_dut
`ifdef WITH_REFERENCE
        sdram #(.SINGLE_CHIP(1)) dut (
`else
        initial begin $display("ERROR reference not compiled"); $finish; end
        psx_sdram_ctrl dut (
`endif
            .init(init), .clk(clk3), .clk_base(clk1), .SDRAM_EN(1'b1),
            .SDRAM_DQ(dq), .SDRAM_A(a), .SDRAM_DQML(dqml), .SDRAM_DQMH(dqmh), .SDRAM_BA(ba), .SDRAM_nCS(ncs),
            .SDRAM_nWE(nwe), .SDRAM_nRAS(nras), .SDRAM_nCAS(ncas), .SDRAM_CKE(cke), .SDRAM_CLK(chip_clk),
            .refreshForce(refresh_force), .ram_idle(ram_idle),
            .ch1_addr(ch1_addr), .ch1_dout(ch1_dout), .ch1_dout32(ch1_dout32), .ch1_din(16'h0), .ch1_req(ch1_req),
            .ch1_rnw(1'b1), .ch1_dma(ch1_dma), .ch1_cntDMA(ch1_cnt), .ch1_cache(ch1_cache), .ch1_ready(ch1_ready),
            .cache_wr(cache_wr), .cache_data(cache_data), .cache_addr(cache_addr),
            .dma_wr(dma_wr), .dma_reqprocessed(dma_rp), .dma_data(dma_data),
            .ch2_addr(ch2_addr), .ch2_dout(ch2_dout), .ch2_din(ch2_din), .ch2_req(ch2_req), .ch2_rnw(1'b0),
            .ch2_be(ch2_be), .ch2_ready(ch2_ready),
            .ch3_addr(ch3_addr), .ch3_dout(ch3_dout), .ch3_din(ch3_din), .ch3_req(ch3_req), .ch3_rnw(ch3_rnw),
            .ch3_be(ch3_be), .ch3_ready(ch3_ready),
            .dmafifo_adr(fifo_adr[fifo_r[3:0]]), .dmafifo_data(fifo_dat[fifo_r[3:0]]),
            .dmafifo_empty(fifo_empty), .dmafifo_read(fifo_read));
    end else begin : new_dut
        psx_sdram_ctrl #(.CAS_LATENCY(CAS), .SPU_FIRST(SPU_FIRST), .OPEN_ROW(OPEN_ROW)) dut (
            .init(init), .clk(clk3), .clk_base(clk1), .SDRAM_EN(1'b1),
            .SDRAM_DQ(dq), .SDRAM_A(a), .SDRAM_DQML(dqml), .SDRAM_DQMH(dqmh), .SDRAM_BA(ba), .SDRAM_nCS(ncs),
            .SDRAM_nWE(nwe), .SDRAM_nRAS(nras), .SDRAM_nCAS(ncas), .SDRAM_CKE(cke), .SDRAM_CLK(chip_clk),
            .refreshForce(refresh_force), .ram_idle(ram_idle),
            .ch1_addr(ch1_addr), .ch1_dout(ch1_dout), .ch1_dout32(ch1_dout32), .ch1_din(16'h0), .ch1_req(ch1_req),
            .ch1_rnw(1'b1), .ch1_dma(ch1_dma), .ch1_cntDMA(ch1_cnt), .ch1_cache(ch1_cache), .ch1_ready(ch1_ready),
            .cache_wr(cache_wr), .cache_data(cache_data), .cache_addr(cache_addr),
            .dma_wr(dma_wr), .dma_reqprocessed(dma_rp), .dma_data(dma_data),
            .ch2_addr(ch2_addr), .ch2_dout(ch2_dout), .ch2_din(ch2_din), .ch2_req(ch2_req), .ch2_rnw(1'b0),
            .ch2_be(ch2_be), .ch2_ready(ch2_ready),
            .ch3_addr(ch3_addr), .ch3_dout(ch3_dout), .ch3_din(ch3_din), .ch3_req(ch3_req), .ch3_rnw(ch3_rnw),
            .ch3_be(ch3_be), .ch3_ready(ch3_ready),
            .dmafifo_adr(fifo_adr[fifo_r[3:0]]), .dmafifo_data(fifo_dat[fifo_r[3:0]]),
            .dmafifo_empty(fifo_empty), .dmafifo_read(fifo_read));
    end endgenerate

    // The chip clock reaches the chip DCLK after the FPGA pin (output buffer
    // and board); commands are modelled as arriving with it.
    wire chip_clk_d;
    assign #(DCLK) chip_clk_d = chip_clk;

    // The reference leaves a few cycles less than 80 ns after REFRESH.
    sdram_chip_model #(.STRICT_RFC(!USE_REF), .NAME(PREFIX), .CAS(CAS), .T_AC(TAC), .T_OH(TOH), .T_RET(TRET)) chip (
        .clk(chip_clk_d), .cke(cke), .ncs(1'b0), .nras(nras), .ncas(ncas), .nwe(nwe),
        .a(a), .ba(ba), .dqm({dqmh, dqml}), .dq(dq));

    // ---------------------------------------------------- golden memory
    reg [15:0] gold [0:(1<<18)-1];
    integer i, fd, cyc1 = 0, cyc3 = 0, local_errors = 0;
    // DQ contention: the controller driving the bus while the chip still
    // drives a read beat (the chip releases T_AC + T_RET after the edge after
    // its last beat).
    generate if (!USE_REF) begin : contention
        always @(chip.drive or new_dut.dut.dq_oe)
            if (chip.drive && new_dut.dut.dq_oe) begin
                local_errors = local_errors + 1;
                $display("ERROR %0s: DQ contention at %0t (chip still driving a read beat)", PREFIX, $time);
            end
    end endgenerate

    initial errors = 0;
    initial begin
        for (i = 0; i < (1<<18); i = i + 1) gold[i] = (i * 16'h9e37) ^ (i >> 5);
        for (i = 0; i < 16; i = i + 1) begin fifo_adr[i] = 0; fifo_dat[i] = 0; end
        fd = $fopen({PREFIX, ".log"}, "w");
    end
    always @(posedge clk1) cyc1 <= cyc1 + 1;
    always @(posedge clk3) cyc3 <= cyc3 + 1;

    function [17:0] gi(input [26:0] adr);      // halfword index as the chip model stores it
        gi = OPEN_ROW != 0 ? {adr[24], adr[10], adr[16:11], adr[25], adr[9:1]} : {adr[24:23], adr[15:10], adr[25], adr[9:1]};
    endfunction
    function [31:0] gold32(input [26:0] adr);  // [15:0] = halfword, [31:16] = its pair
        gold32 = {gold[gi(adr) ^ 18'd1], gold[gi(adr)]};
    endfunction
    task automatic gold_write(input [26:0] adr, input [31:0] d, input [3:0] be);
        reg [15:0] h;
        begin
            h = gold[gi(adr)];
            if (be[0]) h[7:0] = d[7:0];
            if (be[1]) h[15:8] = d[15:8];
            gold[gi(adr)] = h;
            h = gold[gi(adr) | 18'd1];
            if (be[2]) h[7:0] = d[23:16];
            if (be[3]) h[15:8] = d[31:24];
            gold[gi(adr) | 18'd1] = h;
        end
    endtask
    task automatic bad(input [8*48-1:0] what, input [31:0] got, input [31:0] want);
        begin
            local_errors = local_errors + 1;
            if (local_errors <= 20) $display("ERROR %0s clk1x %0d: %0s got %h want %h", PREFIX, cyc1, what, got, want);
        end
    endtask

    // Random addresses: bank (2 bits) is the region, row high bits random.
    // OPEN_ROW: few rows so accesses hit, miss and share open rows; the DMA
    // FIFO region moves into main RAM (rows 8-11), since the OPEN_ROW map
    // puts bit 23 into the row and bank 3 holds SPU-region rows.
    function [26:0] rand_addr(input [1:0] bank, input integer r1, input integer r2);
        if (OPEN_ROW != 0)
            rand_addr = {r2[8], r2[7], bank == 3 ? 2'd0 : bank, bank == 3 ? 13'd8 + r1[1:0] : {11'd0, r1[1:0]}, r2[16:9], 2'b00};
        else
            rand_addr = {r2[8], r2[7], bank, r1[12:0], r2[16:9], 2'b00};
    endfunction

    // -------------------------------------------------- cache line capture
    reg [31:0] line_word [0:3];
    reg [3:0] line_seen = 0;
    reg [7:0] line_index = 0;
    always @(posedge clk3) if (cache_wr != 0) begin
        for (i = 0; i < 4; i = i + 1) if (cache_wr[i]) line_word[i] <= cache_data;
        line_seen <= line_seen | cache_wr;
        line_index <= cache_addr;
        if (line_seen != 0 && cache_addr != line_index) bad("cache_addr changed during a fill", cache_addr, line_index);
        if ((cache_wr & (cache_wr - 1)) != 0) bad("cache_wr not one-hot", cache_wr, 0);
    end

    // ------------------------------------------- pulse width / misuse checks
    reg ch1_ready_d = 0, ch2_ready_d = 0, ch3_ready_d = 0, dma_rp_d = 0;
    reg [31:0] dout_d = 0;
    always @(posedge clk1) begin
        ch1_ready_d <= ch1_ready; ch2_ready_d <= ch2_ready; ch3_ready_d <= ch3_ready; dma_rp_d <= dma_rp;
        dout_d <= ch1_dout32;
        if (ch1_ready && ch1_ready_d) bad("ch1_ready wider than one clk1x", 1, 0);
        if (ch2_ready && ch2_ready_d) bad("ch2_ready wider than one clk1x", 1, 0);
        if (ch3_ready && ch3_ready_d) bad("ch3_ready wider than one clk1x", 1, 0);
        if (dma_rp && dma_rp_d) bad("dma_reqprocessed wider than one clk1x", 1, 0);
        if (ch1_dout32 != dout_d && !ch1_ready && cyc1 > 2) bad("ch1_dout32 changed without ch1_ready", ch1_dout32, dout_d);
    end

    // ------------------------------------------------------ CPU requester
    task automatic wait_ready(output integer at);
        begin
            @(posedge clk1); while (!ch1_ready) @(posedge clk1);
            at = cyc1;
        end
    endtask
    integer seed1 = SEED, seed3 = SEED + 77, seedf = SEED + 991;
    integer ops_left = OPS;
    reg cpu_done = 0, spu_done = 0, fifo_done = 0;
    // DMA expectation queue (declared before the requester that fills it)
    reg [26:0] dma_expect [0:63];
    integer dma_issue [0:63];
    integer exp_w = 0, exp_r = 0, dma_checked = 0;
    integer dma_word_k [0:63];
    initial begin : cpu
        integer op, r1, r2, issue, fin, k, w, cnt, chain, gap;
        reg [26:0] adr;
        reg [31:0] want, d;
        reg [3:0] be;
        repeat (40) @(posedge clk1);
        // wait for the controllers to come out of initialisation
        repeat (8000) @(posedge clk1);
        while (ops_left > 0) begin
            ops_left = ops_left - 1;
            op = $unsigned($random(seed1)) % 10;
            r1 = $random(seed1); r2 = $random(seed1);
            adr = rand_addr({1'b0, r1[30]}, r1, r2);
            gap = $unsigned($random(seed1)) % 4;
            if (op < 3) begin                     // scalar read
                adr[1:0] = 0;
                @(posedge clk1); ch1_addr <= adr; ch1_cache <= 0; ch1_dma <= 0; ch1_req <= 1; issue = cyc1 + 1;
                @(posedge clk1); ch1_req <= 0;
                while (!ch1_ready) @(posedge clk1);
                fin = cyc1;
                if (ch1_dout32 !== gold32(adr)) bad("scalar read", ch1_dout32, gold32(adr));
                $fdisplay(fd, "read %0d %0d", issue, fin);
            end else if (op < 5) begin            // I-cache fill, critical word first
                adr[1:0] = 0;
                w = adr[3:2];
                line_seen = 0;
                @(posedge clk1); ch1_addr <= adr; ch1_cache <= 1; ch1_dma <= 0; ch1_req <= 1; issue = cyc1 + 1;
                @(posedge clk1); ch1_req <= 0;
                while (!ch1_ready) @(posedge clk1);
                fin = cyc1;
                if (ch1_dout32 !== gold32(adr)) bad("cache read word", ch1_dout32, gold32(adr));
                if (!line_seen[w]) bad("cache: requested word not written by ready", line_seen, 1 << w);
                if (w < 3 && !line_seen[w+1]) bad("cache: next word not written by ready", line_seen, 1 << (w+1));
                repeat (3) @(posedge clk1);
                for (k = w; k < 4; k = k + 1) begin
                    want = gold32({adr[26:4], k[1:0], 2'b00});
                    if (line_word[k] !== want) bad("cache line word", line_word[k], want);
                end
                if (line_seen != (4'b1111 << w)) bad("cache: wrong words written", line_seen, 4'b1111 << w);
                if (line_index != adr[11:4]) bad("cache_addr", line_index, adr[11:4]);
                $fdisplay(fd, "cache %0d %0d", issue, fin);
            end else if (op < 7) begin            // DMA reads, back to back
                chain = 1 + $unsigned($random(seed1)) % 3;
                adr[1:0] = 0;
                for (k = 0; k < chain; k = k + 1) begin
                    cnt = $unsigned($random(seed1)) % 4;
                    if (adr[9:2] + cnt > 255) cnt = 255 - adr[9:2];   // DMA clamps at 1 KiB
                    @(posedge clk1); ch1_addr <= adr; ch1_cache <= 0; ch1_dma <= 1; ch1_cnt <= cnt; ch1_req <= 1;
                    issue = cyc1 + 1;
                    for (w = 0; w <= cnt; w = w + 1) begin
                        dma_expect[exp_w & 63] = adr + 4*w; dma_issue[exp_w & 63] = issue; dma_word_k[exp_w & 63] = w;
                        exp_w = exp_w + 1;
                    end
                    @(posedge clk1); ch1_req <= 0;
                    while (!dma_rp) @(posedge clk1);
                    $fdisplay(fd, "dmarp %0d %0d", issue, cyc1);
                    adr = adr + 4*(cnt+1);
                    if (adr[9:2] == 0) adr = adr - 16;
                end
                repeat (12) @(posedge clk1);
                if (exp_r != exp_w) bad("DMA words missing", exp_w - exp_r, 0);
                ch1_dma <= 0;
            end else begin                        // posted write
                be = $random(seed1); if (be == 0) be = 4'b1111;
                adr[1:0] = 0; d = $random(seed1);
                @(posedge clk1); ch2_addr <= adr; ch2_din <= d; ch2_be <= be; ch2_req <= 1; issue = cyc1 + 1;
                gold_write(adr, d, be);
                @(posedge clk1); ch2_req <= 0;
                while (!ch2_ready) @(posedge clk1);
                $fdisplay(fd, "write %0d %0d", issue, cyc1);
            end
            repeat (gap) @(posedge clk1);
        end
        cpu_done = 1;
    end

    // DMA data check: the requester queues the address of every word it
    // asked for; delivered words must come back in that order.
    always @(posedge clk1) if (dma_wr) begin
        if (exp_r == exp_w) bad("unexpected DMA word", dma_data, 0);
        else if (dma_data !== gold32(dma_expect[exp_r & 63])) bad("DMA word", dma_data, gold32(dma_expect[exp_r & 63]));
        $fdisplay(fd, "dmaword%0d %0d %0d", dma_word_k[exp_r & 63], dma_issue[exp_r & 63], cyc1);
        exp_r = exp_r + 1;
        dma_checked = dma_checked + 1;
    end

    // ------------------------------------------------------ SPU / loader
    initial begin : spu
        integer r1, r2, issue, kind;
        reg [26:0] adr;
        reg [31:0] d, want;
        reg [3:0] be;
        // A loader word issued during initialisation must be served after it.
        repeat (5) @(posedge clk1);
        adr = rand_addr(2'd2, 32'h1234, 32'h40); adr[1:0] = 0; d = 32'hcafef00d;
        ch3_addr <= adr; ch3_din <= d; ch3_be <= 4'b1111; ch3_rnw <= 0; ch3_req <= 1; issue = cyc1 + 1;
        gold_write(adr, d, 4'b1111);
        @(posedge clk1); ch3_req <= 0;
        while (!ch3_ready) @(posedge clk1);
        $fdisplay(fd, "boot %0d %0d", issue, cyc1);
        repeat (8000) @(posedge clk1);
        while (!cpu_done) begin
            r1 = $random(seed3); r2 = $random(seed3);
            adr = rand_addr(2'd2, r1, r2);
            kind = $unsigned($random(seed3)) % 4;
            if (kind == 0) begin                  // loader-style word write
                adr[1:0] = 0; be = $random(seed3); if (be == 0) be = 4'b1111;
            end else begin                        // SPU halfword
                adr[1:0] = {r2[0], 1'b0}; be = 4'b0011;
            end
            d = $random(seed3);
            @(posedge clk1);
            ch3_addr <= adr; ch3_din <= d; ch3_be <= be; ch3_rnw <= kind == 3; ch3_req <= 1; issue = cyc1 + 1;
            if (kind != 3) gold_write(adr, d, be);
            want = gold32(adr);
            @(posedge clk1); ch3_req <= 0;
            while (!ch3_ready) @(posedge clk1);
            if (kind == 3) begin
                if (ch3_dout !== want) bad("ch3 read", ch3_dout, want);
                $fdisplay(fd, "ch3read %0d %0d", issue, cyc1);
            end else $fdisplay(fd, "ch3write %0d %0d", issue, cyc1);
            repeat ($unsigned($random(seed3)) % 12) @(posedge clk1);
        end
        spu_done = 1;
    end

    // ----------------------------------------------------- DMA write FIFO
    always @(posedge clk3) if (fifo_read) begin
        if (fifo_empty) bad("dmafifo_read on an empty FIFO", 1, 0);
        fifo_r <= fifo_r + 1'b1;
    end
    reg fifo_read_d = 0;
    always @(posedge clk3) begin
        fifo_read_d <= fifo_read;
        if (fifo_read && fifo_read_d) bad("dmafifo_read on two consecutive clk", 1, 0);
    end
    initial begin : dmawrite
        integer r1, r2, run, k;
        reg [26:0] adr;
        reg [31:0] d;
        repeat (24000) @(posedge clk3);
        while (!cpu_done) begin
            r1 = $random(seedf); r2 = $random(seedf);
            adr = rand_addr(2'd3, r1, r2); adr[1:0] = 0;
            run = 1 + $unsigned($random(seedf)) % 40;
            for (k = 0; k < run; k = k + 1) begin
                // fifo_w may still be pending from the previous push in this time step
                while (((fifo_w - fifo_r) & 5'h1f) >= 15) @(posedge clk3);
                d = $random(seedf);
                @(posedge clk3);
                fifo_adr[fifo_w[3:0]] <= adr; fifo_dat[fifo_w[3:0]] <= d; fifo_w <= fifo_w + 1'b1;
                gold_write(adr, d, 4'b1111);
                adr = adr + 4;
                if ($unsigned($random(seedf)) % 7 == 0) repeat ($unsigned($random(seedf)) % 30) @(posedge clk3);
            end
            repeat ($unsigned($random(seedf)) % 400) @(posedge clk3);
        end
        fifo_done = 1;
    end

    // --------------------------------------------------- refresh pulses
    initial begin : force_refresh
        repeat (9000) @(posedge clk1);
        while (!cpu_done) begin
            repeat (2000 + $unsigned($random(seedf)) % 3000) @(posedge clk1);
            refresh_force <= 1;
            repeat (1 + $unsigned($random(seedf)) % 6) @(posedge clk1);
            refresh_force <= 0;
        end
    end

    // --------------------------------------------------------- finish
    initial begin
        repeat (6) @(posedge clk3);
        init <= 0;
        wait (cpu_done && spu_done && fifo_done);
        while (!fifo_empty) @(posedge clk3);
        repeat (200) @(posedge clk3);
        for (i = 0; i < (1<<18); i = i + 1)
            if (chip.mem[i] !== gold[i]) begin bad("memory contents", chip.mem[i], gold[i]); $display("  index %h (bank %0d row6 %0d col %0d)", i, i[17:16], i[15:10], i[9:0]); end
        errors = local_errors + chip.errors;
        $fdisplay(fd, "summary clk3 %0d refreshes %0d reads %0d writes %0d dma_words %0d tRFC_short %0d errors %0d",
                  cyc3, chip.refreshes, chip.reads, chip.writes, dma_checked, chip.warnings, errors);
        $fclose(fd);
        done = 1;
    end
endmodule

module tb_psx_sdram_ctrl #(parameter integer OPS = 4000, parameter integer SEED = 1, parameter integer CAS = 2, parameter integer SPU_FIRST = 0, parameter integer OPEN_ROW = 0,
                          parameter real DCLK = 2.0, TAC = 6.0, TOH = 2.5, TRET = 2.0);
    reg clk3 = 0, clk1 = 0;
    always #4.921 clk3 = ~clk3;     // 101.6064 MHz
    always #14.763 clk1 = ~clk1;    // 33.8688 MHz, edges aligned
    wire done_new, done_ref;
    integer err_new, err_ref;
    integer seed;
    sdram_bench #(.USE_REF(0), .PREFIX("ours"), .OPS(OPS), .SEED(SEED), .CAS(CAS), .SPU_FIRST(SPU_FIRST), .OPEN_ROW(OPEN_ROW), .DCLK(DCLK), .TAC(TAC), .TOH(TOH), .TRET(TRET)) ours (.clk3(clk3), .clk1(clk1), .done(done_new), .errors(err_new));
`ifdef WITH_REFERENCE
    sdram_bench #(.USE_REF(1), .PREFIX("reference"), .OPS(OPS), .SEED(SEED), .CAS(2), .DCLK(DCLK), .TAC(TAC), .TOH(TOH), .TRET(TRET)) reference (.clk3(clk3), .clk1(clk1), .done(done_ref), .errors(err_ref));
`else
    assign done_ref = 1;
    initial err_ref = 0;
`endif
    initial begin
        wait (done_new && done_ref);
        $display("RESULT errors ours %0d reference %0d", err_new, err_ref);
        $finish;
    end
    initial begin
        #200000000;
        $display("ERROR timeout");
        $finish;
    end
endmodule
`default_nettype wire
