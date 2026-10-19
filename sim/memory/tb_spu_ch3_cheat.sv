// SPDX-License-Identifier: MIT
// psx_spu_ch3_cache_slim CHEAT=1 on psx_sdram_ctrl (CAS3, SPU_FIRST) and the
// chip model: the SPU-like source of tb_psx_spu_ch3_cache (24 voices, capture
// writes, overwrite and read back) runs while a cheat client makes random
// main RAM halfword and byte reads and writes, one at a time, as the
// firmware does. Both are checked against a golden copy; the SPU must still
// finish every 768-clk1x tick. A guest reset (discard) in the middle must
// answer a waiting cheat access with 0. A copy that takes cheat accesses
// while an SPU request is pending is caught.
// Reports errors, late ticks, cheat accesses and discard answers.
`timescale 1ns/1ps
`default_nettype none
module tb_spu_ch3_cheat #(parameter integer TICKS = 60, parameter integer SEED = 1);
    reg clk3 = 0, clk1 = 0;
    always #4.921 clk3 = ~clk3;
    always #14.763 clk1 = ~clk1;
    wire [15:0] dq; wire [12:0] a; wire [1:0] ba; wire dqml, dqmh, nwe, nras, ncas, cke, chip_clk;
    reg init = 1, discard = 0;
    wire [31:0] ch1_dout32, ch3_dout, dma_data, cache_data; wire [127:0] ch1_dout;
    wire ch1_ready, ch2_ready, ch3_ready, dma_wr, dma_rp; wire [3:0] cache_wr; wire [7:0] cache_addr;
    wire [26:0] ch3_addr; wire [31:0] ch3_din; wire [3:0] ch3_be; wire ch3_req, ch3_rnw;
    psx_sdram_ctrl #(.CAS_LATENCY(3), .SPU_FIRST(1)) ctrl (
        .init(init), .clk(clk3), .clk_base(clk1), .SDRAM_EN(1'b1),
        .SDRAM_DQ(dq), .SDRAM_A(a), .SDRAM_DQML(dqml), .SDRAM_DQMH(dqmh), .SDRAM_BA(ba), .SDRAM_nCS(),
        .SDRAM_nWE(nwe), .SDRAM_nRAS(nras), .SDRAM_nCAS(ncas), .SDRAM_CKE(cke), .SDRAM_CLK(chip_clk),
        .refreshForce(1'b0), .ram_idle(),
        .ch1_addr(27'd0), .ch1_dout(ch1_dout), .ch1_dout32(ch1_dout32), .ch1_din(16'h0), .ch1_req(1'b0),
        .ch1_rnw(1'b1), .ch1_dma(1'b0), .ch1_cntDMA(2'd0), .ch1_cache(1'b0), .ch1_ready(ch1_ready),
        .cache_wr(cache_wr), .cache_data(cache_data), .cache_addr(cache_addr),
        .dma_wr(dma_wr), .dma_reqprocessed(dma_rp), .dma_data(dma_data),
        .ch2_addr(27'd0), .ch2_dout(), .ch2_din(32'd0), .ch2_req(1'b0), .ch2_rnw(1'b0), .ch2_be(4'd0), .ch2_ready(ch2_ready),
        .ch3_addr(ch3_addr), .ch3_dout(ch3_dout), .ch3_din(ch3_din), .ch3_req(ch3_req), .ch3_rnw(ch3_rnw),
        .ch3_be(ch3_be), .ch3_ready(ch3_ready),
        .dmafifo_adr(27'd0), .dmafifo_data(32'd0), .dmafifo_empty(1'b1), .dmafifo_read());
    wire chip_clk_d; assign #(2.0) chip_clk_d = chip_clk;
    sdram_chip_model #(.STRICT_RFC(1), .NAME("cheat"), .CAS(3), .T_AC(6.0), .T_OH(2.5), .T_RET(2.0)) chip (
        .clk(chip_clk_d), .cke(cke), .ncs(1'b0), .nras(nras), .ncas(ncas), .nwe(nwe),
        .a(a), .ba(ba), .dqm({dqmh, dqml}), .dq(dq));

    reg spu_req = 0, spu_rnw = 1; reg [18:0] spu_addr = 0; reg [31:0] spu_data = 0; reg [3:0] spu_be = 4'b0011;
    wire spu_done; wire [31:0] spu_q;
    reg ch_req = 0, ch_rnw = 1, ch_byte = 0; reg [20:0] ch_addr = 0; reg [15:0] ch_data = 0;
    wire ch_done; wire [15:0] ch_q;
    psx_spu_ch3_cache #(.CHEAT(1)) dut (.clk1x(clk1), .reset(init), .discard(discard),
        .spu_req(spu_req), .spu_rnw(spu_rnw), .spu_addr(spu_addr), .spu_data(spu_data), .spu_be(spu_be),
        .spu_done(spu_done), .spu_q(spu_q),
        .ch3_req(ch3_req), .ch3_rnw(ch3_rnw), .ch3_addr(ch3_addr), .ch3_din(ch3_din), .ch3_be(ch3_be),
        .ch3_ready(ch3_ready && !discard), .ch3_dout(ch3_dout), .stat_access(), .stat_hit(),
        .ch_req(ch_req), .ch_rnw(ch_rnw), .ch_byte(ch_byte), .ch_addr(ch_addr), .ch_data(ch_data),
        .ch_done(ch_done), .ch_q(ch_q));

    reg [15:0] gold [0:(1<<18)-1];
    integer i, errors = 0, cyc1 = 0, cheats = 0, zeros = 0;
    always @(posedge clk1) cyc1 <= cyc1 + 1;
    function [17:0] gi(input [26:0] adr); gi = {adr[24:23], adr[15:10], adr[25], adr[9:1]}; endfunction
    initial for (i = 0; i < (1<<18); i = i + 1) gold[i] = (i * 16'h9e37) ^ (i >> 5);
    task bad(input [31:0] got, input [31:0] want, input [26:0] adr);
        begin errors = errors + 1; if (errors <= 10) $display("ERROR clk1x %0d addr %h got %h want %h", cyc1, adr, got, want); end
    endtask
    // A pending SPU request must win: no cheat access starts while one waits.
    always @(posedge clk1) if (ch3_req && ch3_addr[24] == 0 && dut.pend) begin
        errors = errors + 1; $display("ERROR clk1x %0d cheat access taken over a pending SPU request", cyc1);
    end

    task spu_access(input rnw, input [18:0] adr, input [15:0] d);
        reg [26:0] full; reg [15:0] want;
        begin
            full = 27'h1000000 + {8'b0, adr};
            @(posedge clk1);
            spu_req <= 1; spu_rnw <= rnw; spu_addr <= adr; spu_data <= {16'h0, d}; spu_be <= 4'b0011;
            if (!rnw) gold[gi(full)] = d;
            want = gold[gi(full)];
            @(posedge clk1); spu_req <= 0;
            while (!spu_done) @(posedge clk1);
            if (rnw && spu_q[15:0] !== want) bad(spu_q, want, full);
        end
    endtask

    // cheat client: random accesses in the first 64 KiB of main RAM (the
    // chip model's bank 0; SPU RAM is in bank 2)
    integer s3 = SEED + 13, running = 1, in_discard = 0;
    // discard high in one of the last three clocks: an answer then is 0
    reg [2:0] disc_seen = 0;
    always @(posedge clk1) disc_seen <= {disc_seen[1:0], discard};
    task cheat_access(input rnw, input byte_, input [20:0] adr, input [15:0] d);
        reg [26:0] full; reg [15:0] old, want;
        begin
            full = {6'b0, adr[20:1], 1'b0};
            old = gold[gi(full)];
            @(posedge clk1);
            ch_req <= 1; ch_rnw <= rnw; ch_byte <= byte_; ch_addr <= adr; ch_data <= d;
            @(posedge clk1); ch_req <= 0;
            while (!ch_done) @(posedge clk1);
            if (discard || disc_seen != 0) begin
                if (ch_q !== 0) bad(ch_q, 0, full);
                zeros = zeros + 1;
            end else begin
                if (!rnw) gold[gi(full)] = !byte_ ? d : adr[0] ? {d[7:0], old[7:0]} : {old[15:8], d[7:0]};
                want = gold[gi(full)];
                if (rnw && ch_q !== want) bad(ch_q, want, full);
                cheats = cheats + 1;
            end
        end
    endtask
    initial begin
        wait (!init); repeat (31000) @(posedge clk1);
        while (running) begin
            if (in_discard) cheat_access(1, 0, 21'h100, 0);
            else cheat_access($random(s3) & 1, $random(s3) & 1, $random(s3) & 21'h00ffff, $random(s3));
            repeat ($random(s3) & 15) @(posedge clk1);
        end
    end

    integer t, v, start, vstart, used, late = 0, s2 = SEED + 7;
    reg [18:0] blk [0:23]; integer smp [0:23];
    initial begin
        for (v = 0; v < 24; v = v + 1) begin blk[v] = 19'h1000 + v * 19'h800; smp[v] = v; end
        repeat (20) @(posedge clk1); init = 0;
        repeat (30000) @(posedge clk1);
        for (t = 0; t < TICKS; t = t + 1) begin
            start = cyc1;
            for (v = 0; v < 24; v = v + 1) begin
                vstart = cyc1;
                spu_access(1, blk[v], 0);
                spu_access(1, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, 0);
                smp[v] = smp[v] + 1;
                if (smp[v] >= 28) begin smp[v] = smp[v] - 28; blk[v] = blk[v] + 19'd16; end
            end
            for (v = 0; v < 4; v = v + 1) spu_access(0, {8'h0, v[1:0], t[7:0], 1'b0}, $random(s2));
            v = t % 24;
            spu_access(0, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, $random(s2));
            spu_access(1, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, 0);
            used = cyc1 - start;
            if (used > 768) late = late + 1;
            while (cyc1 - start < 768) @(posedge clk1);
            if (t == TICKS / 2) begin
                // a guest reset: the SPU idles, waiting cheat accesses get 0
                // (the cheat client only reads once in_discard is set)
                in_discard = 1; repeat (200) @(posedge clk1);
                @(posedge clk1); discard <= 1;
                repeat (400) @(posedge clk1);
                @(posedge clk1); discard <= 0;
                repeat (200) @(posedge clk1); in_discard = 0;
                repeat (200) @(posedge clk1);
            end
        end
        running = 0;
        repeat (400) @(posedge clk1);
        $display("RESULT errors %0d late_ticks %0d cheats %0d discard_zeros %0d", errors, late, cheats, zeros);
        $finish;
    end
    initial begin #400000000; $display("ERROR timeout"); $finish; end
endmodule
`default_nettype wire
