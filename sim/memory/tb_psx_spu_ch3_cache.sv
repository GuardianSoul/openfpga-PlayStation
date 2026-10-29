// SPDX-License-Identifier: MIT
// psx_spu_ch3_cache on psx_sdram_ctrl (CAS3, SPU_FIRST) and the chip model,
// against a golden copy. An SPU-like source issues one access at a time and
// waits for done (as MiSTer spu_ram does): per 768-clk1x tick, 24 voices
// read the next halfword of their own stream (a header every 8), and four
// capture halfwords are written. A background client keeps ch1 busy with
// random reads. Each tick also overwrites one voice's cached data halfword
// and reads it back (write invalidation). CACHE=0 runs the direct ch3 path
// (prepare without --spu-cache) for comparison.
// Reports errors, ticks whose SPU accesses did not finish within 768 clk1x,
// and the mean/max clk1x per tick.
`timescale 1ns/1ps
`default_nettype none
module tb_psx_spu_ch3_cache #(parameter integer CACHE = 1, parameter integer TICKS = 300, parameter integer LOAD = 1,
                           parameter integer SEED = 1, parameter integer PITCH = 1, parameter integer SLOT = 0,
                           parameter integer VOICE_SETS = 0,   // the slim cache's VOICE_SETS
                           parameter integer LAYOUT = 0,       // 0: voices 2 KiB apart; 1: hash-colliding pairs, voices 0/1 share one block
                           parameter integer WRITE_EVERY = 1); // ticks between the write rounds (the release spu_ram keeps capture in M10K,
                                                               // so ch3 sees writes only from transfers)
    reg clk3 = 0, clk1 = 0;
    always #4.921 clk3 = ~clk3;
    always #14.763 clk1 = ~clk1;
    wire [15:0] dq; wire [12:0] a; wire [1:0] ba; wire dqml, dqmh, nwe, nras, ncas, cke, chip_clk;
    reg init = 1;
    reg [26:0] ch1_addr = 0; reg ch1_req = 0;
    wire [31:0] ch1_dout32, ch3_dout, dma_data, cache_data; wire [127:0] ch1_dout;
    wire ch1_ready, ch2_ready, ch3_ready, dma_wr, dma_rp; wire [3:0] cache_wr; wire [7:0] cache_addr;
    wire [26:0] ch3_addr; wire [31:0] ch3_din; wire [3:0] ch3_be; wire ch3_req, ch3_rnw;
    psx_sdram_ctrl #(.CAS_LATENCY(3), .SPU_FIRST(1)) ctrl (
        .init(init), .clk(clk3), .clk_base(clk1), .SDRAM_EN(1'b1),
        .SDRAM_DQ(dq), .SDRAM_A(a), .SDRAM_DQML(dqml), .SDRAM_DQMH(dqmh), .SDRAM_BA(ba), .SDRAM_nCS(),
        .SDRAM_nWE(nwe), .SDRAM_nRAS(nras), .SDRAM_nCAS(ncas), .SDRAM_CKE(cke), .SDRAM_CLK(chip_clk),
        .refreshForce(1'b0), .ram_idle(),
        .ch1_addr(ch1_addr), .ch1_dout(ch1_dout), .ch1_dout32(ch1_dout32), .ch1_din(16'h0), .ch1_req(ch1_req),
        .ch1_rnw(1'b1), .ch1_dma(1'b0), .ch1_cntDMA(2'd0), .ch1_cache(1'b0), .ch1_ready(ch1_ready),
        .cache_wr(cache_wr), .cache_data(cache_data), .cache_addr(cache_addr),
        .dma_wr(dma_wr), .dma_reqprocessed(dma_rp), .dma_data(dma_data),
        .ch2_addr(27'd0), .ch2_dout(), .ch2_din(32'd0), .ch2_req(1'b0), .ch2_rnw(1'b0), .ch2_be(4'd0), .ch2_ready(ch2_ready),
        .ch3_addr(ch3_addr), .ch3_dout(ch3_dout), .ch3_din(ch3_din), .ch3_req(ch3_req), .ch3_rnw(ch3_rnw),
        .ch3_be(ch3_be), .ch3_ready(ch3_ready),
        .dmafifo_adr(27'd0), .dmafifo_data(32'd0), .dmafifo_empty(1'b1), .dmafifo_read());
    wire chip_clk_d; assign #(2.0) chip_clk_d = chip_clk;
    sdram_chip_model #(.STRICT_RFC(1), .NAME("spucache"), .CAS(3), .T_AC(6.0), .T_OH(2.5), .T_RET(2.0)) chip (
        .clk(chip_clk_d), .cke(cke), .ncs(1'b0), .nras(nras), .ncas(ncas), .nwe(nwe),
        .a(a), .ba(ba), .dqm({dqmh, dqml}), .dq(dq));

    // SPU port
    reg spu_req = 0, spu_rnw = 1; reg [18:0] spu_addr = 0; reg [31:0] spu_data = 0; reg [3:0] spu_be = 4'b0011;
    reg [4:0] spu_voice = 0; reg spu_isvoice = 0;
    wire spu_done; wire [31:0] spu_q; wire fault; wire stat_access, stat_hit;
    integer reads = 0, hits = 0;
    always @(posedge clk1) begin if (stat_access) reads <= reads + 1; if (stat_hit) hits <= hits + 1; end
    generate if (CACHE) begin : gcache
        if (VOICE_SETS) begin : gvoice   // only the slim cache has the voice ports
        psx_spu_ch3_cache #(.VOICE_SETS(VOICE_SETS)) dut (.clk1x(clk1), .reset(init), .discard(1'b0),
            .spu_req(spu_req), .spu_rnw(spu_rnw), .spu_voice(spu_voice), .spu_isvoice(spu_isvoice), .spu_addr(spu_addr), .spu_data(spu_data), .spu_be(spu_be),
            .spu_done(spu_done), .spu_q(spu_q),
            .ch3_req(ch3_req), .ch3_rnw(ch3_rnw), .ch3_addr(ch3_addr), .ch3_din(ch3_din), .ch3_be(ch3_be),
            .ch3_ready(ch3_ready), .ch3_dout(ch3_dout), .stat_access(stat_access), .stat_hit(stat_hit));
        end else begin : gaddr
        psx_spu_ch3_cache dut (.clk1x(clk1), .reset(init), .discard(1'b0),
            .spu_req(spu_req), .spu_rnw(spu_rnw), .spu_addr(spu_addr), .spu_data(spu_data), .spu_be(spu_be),
            .spu_done(spu_done), .spu_q(spu_q),
            .ch3_req(ch3_req), .ch3_rnw(ch3_rnw), .ch3_addr(ch3_addr), .ch3_din(ch3_din), .ch3_be(ch3_be),
            .ch3_ready(ch3_ready), .ch3_dout(ch3_dout), .stat_access(stat_access), .stat_hit(stat_hit));
        end
        assign fault = 0;
    end else begin : direct
        assign ch3_req = spu_req, ch3_rnw = spu_rnw, ch3_addr = 27'h1000000 + {8'b0, spu_addr};
        assign ch3_din = spu_data, ch3_be = spu_be, spu_done = ch3_ready, spu_q = ch3_dout, fault = 0;
        assign stat_access = 0, stat_hit = 0;
    end endgenerate

    // golden (chip model init and address map, as tb_psx_sdram_ctrl)
    reg [15:0] gold [0:(1<<18)-1];
    integer i, errors = 0, cyc1 = 0;
    always @(posedge clk1) cyc1 <= cyc1 + 1;
    function [17:0] gi(input [26:0] adr); gi = {adr[24:23], adr[15:10], adr[25], adr[9:1]}; endfunction
    initial for (i = 0; i < (1<<18); i = i + 1) gold[i] = (i * 16'h9e37) ^ (i >> 5);
    task bad(input [31:0] got, input [31:0] want, input [26:0] adr);
        begin errors = errors + 1; if (errors <= 10) $display("ERROR clk1x %0d addr %h got %h want %h", cyc1, adr, got, want); end
    endtask

    // background ch1 load
    integer s1 = SEED;
    initial begin
        wait (!init); repeat (2000) @(posedge clk1);
        forever begin
            if (LOAD) begin
                @(posedge clk1); ch1_addr <= {2'b0, ($random(s1) & 32'h0ffffc)}; ch1_req <= 1;
                @(posedge clk1); ch1_req <= 0;
                while (!ch1_ready) @(posedge clk1);
            end else @(posedge clk1);
        end
    end

    task spu_access(input rnw, input [18:0] adr, input [15:0] d);
        spu_voice_access(rnw, adr, d, 0, 0);
    endtask
    task spu_voice_access(input rnw, input [18:0] adr, input [15:0] d, input isv, input [4:0] voice);
        reg [26:0] full; reg [15:0] want;
        begin
            full = 27'h1000000 + {8'b0, adr};
            @(posedge clk1);
            spu_req <= 1; spu_rnw <= rnw; spu_addr <= adr; spu_data <= {16'h0, d}; spu_be <= 4'b0011;
            spu_isvoice <= isv; spu_voice <= voice;
            if (!rnw) gold[gi(full)] = d;
            want = gold[gi(full)];
            @(posedge clk1); spu_req <= 0; spu_isvoice <= 0;   // isvoice is a pulse with the request, as in spu.vhd
            while (!spu_done) @(posedge clk1);
            if (rnw && spu_q[15:0] !== want) bad(spu_q, want, full);
            if (fault) begin errors = errors + 1; $display("ERROR fault"); end
        end
    endtask

    integer t, v, start, vstart, used, maxused = 0, total = 0, late = 0, s2 = SEED + 7;
    reg [18:0] blk [0:23]; integer smp [0:23];
    initial begin
        for (v = 0; v < 24; v = v + 1) begin blk[v] = 19'h1000 + v * 19'h800; smp[v] = v; end   // separate voice streams (the chip model keeps 64 KiB per bank)
        // LAYOUT 1: odd voices on a block whose hashed set equals the even
        // voice's (the 32-set hash XORs 5-bit address groups: flipping bits 9 and 14 keeps it),
        // and voices 0 and 1 on the very same block (one sample, two voices)
        if (LAYOUT == 1) for (v = 1; v < 24; v = v + 2) blk[v] = v == 1 ? blk[0] : blk[v-1] ^ 19'h04200;
        repeat (20) @(posedge clk1); init = 0;
        repeat (30000) @(posedge clk1);
        for (t = 0; t < TICKS; t = t + 1) begin
            start = cyc1;
            for (v = 0; v < 24; v = v + 1) begin
                vstart = cyc1;
                // MiSTer spu_ram (SDRAM mode) reads the voice's ADPCM block
                // header and current data halfword every tick; 28 samples
                // per 16-byte block, 4 per halfword.
                spu_voice_access(1, blk[v], 0, 1, v[4:0]);
                spu_voice_access(1, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, 0, 1, v[4:0]);
                smp[v] = smp[v] + PITCH;
                if (smp[v] >= 28) begin smp[v] = smp[v] - 28; blk[v] = blk[v] + 19'd16; end
                // SLOT>0: spu.vhd with useSDRAM='1' (no --spu-free-slot) holds
                // each voice until about SLOT clk1x after its start (RAM_WAIT).
                while (SLOT > 0 && cyc1 - vstart < SLOT) @(posedge clk1);
            end
            if (t % WRITE_EVERY == 0) begin
            for (v = 0; v < 4; v = v + 1) spu_access(0, {8'h0, v[1:0], t[7:0], 1'b0}, $random(s2));   // capture
            if (t % 16 == 0) spu_access(1, {8'h0, 2'd1, t[7:0], 1'b0}, 0);   // read back capture
            v = t % 24;                                                 // overwrite a cached halfword
            spu_access(0, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, $random(s2));
            spu_voice_access(1, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, 0, 1, v[4:0]);
            end
            // the shared block: written, then read by both its voices
            if (LAYOUT == 1 && t % 8 == 3) begin
                spu_access(0, blk[0], $random(s2));
                spu_voice_access(1, blk[0], 0, 1, 5'd0); spu_voice_access(1, blk[0], 0, 1, 5'd1);
            end
            used = cyc1 - start; total = total + used; if (used > maxused) maxused = used;
            if (used > 768) late = late + 1;
            while (cyc1 - start < 768) @(posedge clk1);
        end
        $display("RESULT slot %0d cache %0d load %0d pitch %0d errors %0d late_ticks %0d mean %0d max %0d reads %0d hits %0d", SLOT, CACHE, LOAD, PITCH, errors, late, total / TICKS, maxused, reads, hits);
        $finish;
    end
    initial begin #400000000; $display("ERROR timeout"); $finish; end
endmodule
`default_nettype wire
