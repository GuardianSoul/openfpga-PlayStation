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
                           parameter integer LAYOUT = 0,       // 0: voices 2 KiB apart; 1: hash-colliding pairs, voices 0/1 share one block;
                                                               // 2: voices 64 bytes apart (all in two 1 KiB regions)
                           parameter integer WRITE_EVERY = 1,  // ticks between the write rounds (the release spu_ram keeps capture in M10K,
                                                               // so ch3 sees writes only from transfers)
                           parameter integer XFER = 0,         // transfer halfwords written per tick, one after each of the first XFER
                                                               // voices (spu.vhd drains its FIFO so), in order through 0xD000-0xDFFF
                                                               // (every 256-block bucket); the last one is read back each tick
                           parameter integer XMODE = 0,        // 1: voice 23 plays that region (a stream written around its voice),
                                                               // and the writes carry the voice flag (spu.vhd's do not)
                           parameter integer POST_WRITE = 0,   // the slim cache's POST_WRITE (with VOICE_SETS=2)
                           parameter integer DIRECTED = 0,     // 1: the directed next-request cases below instead of the ticks
                           parameter integer GAPS = 40,        // DIRECTED: the next request comes 0..GAPS clk1x after the earliest clock
                           parameter integer OPEN_ROW = 0);    // psx_sdram_ctrl's OPEN_ROW (the release instance has 1)
    reg clk3 = 0, clk1 = 0;
    always #4.921 clk3 = ~clk3;
    always #14.763 clk1 = ~clk1;
    wire [15:0] dq; wire [12:0] a; wire [1:0] ba; wire dqml, dqmh, nwe, nras, ncas, cke, chip_clk;
    reg init = 1;
    reg [26:0] ch1_addr = 0; reg ch1_req = 0;
    wire [31:0] ch1_dout32, ch3_dout_sdram, dma_data, cache_data; wire [127:0] ch1_dout;
    // The ch3 read data between the controller and the cache. CH3_PATH: the
    // project's psx_core_memory text for it (run_spu_ch3_cache.py
    // --core-memory writes those lines to ch3_path.svh: a register
    // ch3_dout loaded from ch3_dout_sdram on clk1x).
`ifdef CH3_PATH
    wire clk1x = clk1;
    `include "ch3_path.svh"
`else
    wire [31:0] ch3_dout = ch3_dout_sdram;
`endif
    wire ch1_ready, ch2_ready, ch3_ready, dma_wr, dma_rp; wire [3:0] cache_wr; wire [7:0] cache_addr;
    wire [26:0] ch3_addr; wire [31:0] ch3_din; wire [3:0] ch3_be; wire ch3_req, ch3_rnw;
    psx_sdram_ctrl #(.CS_TIED_LOW(1), .CAS_LATENCY(3), .SPU_FIRST(1), .OPEN_ROW(OPEN_ROW)) ctrl (
        .init(init), .clk(clk3), .clk_base(clk1), .SDRAM_EN(1'b1),
        .SDRAM_DQ(dq), .SDRAM_A(a), .SDRAM_DQML(dqml), .SDRAM_DQMH(dqmh), .SDRAM_BA(ba), .SDRAM_nCS(),
        .SDRAM_nWE(nwe), .SDRAM_nRAS(nras), .SDRAM_nCAS(ncas), .SDRAM_CKE(cke), .SDRAM_CLK(chip_clk),
        .refreshForce(1'b0), .ram_idle(),
        .ch1_addr(ch1_addr), .ch1_dout(ch1_dout), .ch1_dout32(ch1_dout32), .ch1_din(16'h0), .ch1_req(ch1_req),
        .ch1_rnw(1'b1), .ch1_dma(1'b0), .ch1_cntDMA(2'd0), .ch1_cache(1'b0), .ch1_ready(ch1_ready),
        .cache_wr(cache_wr), .cache_data(cache_data), .cache_addr(cache_addr),
        .dma_wr(dma_wr), .dma_reqprocessed(dma_rp), .dma_data(dma_data),
        .ch2_addr(27'd0), .ch2_dout(), .ch2_din(32'd0), .ch2_req(1'b0), .ch2_rnw(1'b0), .ch2_be(4'd0), .ch2_ready(ch2_ready),
        .ch3_addr(ch3_addr), .ch3_dout(ch3_dout_sdram), .ch3_din(ch3_din), .ch3_req(ch3_req), .ch3_rnw(ch3_rnw),
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
        if (VOICE_SETS && POST_WRITE) begin : gpost   // a cache from before POST_WRITE has no such parameter
        psx_spu_ch3_cache #(.VOICE_SETS(VOICE_SETS), .POST_WRITE(POST_WRITE)) dut (.clk1x(clk1), .reset(init), .discard(1'b0),
            .spu_req(spu_req), .spu_rnw(spu_rnw), .spu_voice(spu_voice), .spu_isvoice(spu_isvoice), .spu_addr(spu_addr), .spu_data(spu_data), .spu_be(spu_be),
            .spu_done(spu_done), .spu_q(spu_q),
            .ch3_req(ch3_req), .ch3_rnw(ch3_rnw), .ch3_addr(ch3_addr), .ch3_din(ch3_din), .ch3_be(ch3_be),
            .ch3_ready(ch3_ready), .ch3_dout(ch3_dout), .stat_access(stat_access), .stat_hit(stat_hit));
        end else if (VOICE_SETS) begin : gvoice   // only the slim cache has the voice ports
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
    // OPEN_ROW has its own address map (bank {[24],[10]}, row from [11]), as in tb_psx_sdram_ctrl
    function [17:0] gi(input [26:0] adr); gi = OPEN_ROW != 0 ? {adr[24], adr[10], adr[16:11], adr[25], adr[9:1]} : {adr[24:23], adr[15:10], adr[25], adr[9:1]}; endfunction
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
    // DIRECTED: what follows a write, at every distance. A write to X (not a
    // voice's, as spu.vhd's transfer writes and the save port's) and then
    //   read-same:    a read of X;
    //   read-line:    a read of each of the seven other halfwords of X's 16-byte
    //                 line, then of X;
    //   read-region:  a voice's read of Y in another line of X's 1 KiB region
    //                 (one owner-table entry), a write to Y, the voice's read of Y,
    //                 then a read of X;
    //   write-read:   a second write to X with other data, then a read of X.
    // The request after a write is raised g clk1x after the earliest clock the
    // port allows, g = 0..GAPS: g = 0 is the clock on which the SPU sees the
    // write's done (spu.vhd raises ram_request from a state that waits for
    // ram_done). write-read takes every pair of gaps. Each case runs with X (and
    // Y) read before the write and not, and with the reads as a voice's (its own
    // set) and as no voice's (set 31). Every read is compared with the golden
    // copy, and all the lines used are read back from SDRAM at the end, so a
    // write that lands late, twice or elsewhere is seen as well.
    integer wr_start = 0, wr_issued = 0, wr_landed = 0, wr_min = 1000, wr_max = 0, wr_open = 0;
    integer wr_hist [0:63], di;
    initial for (di = 0; di < 64; di = di + 1) wr_hist[di] = 0;
    always @(posedge clk1) begin   // clk1x from a ch3 write's request to its ready
        if (ch3_req && !ch3_rnw) begin wr_start <= cyc1; wr_open <= 1; end
        if (ch3_ready && wr_open) begin
            wr_open <= 0; wr_landed <= wr_landed + 1;
            if (cyc1 - wr_start < wr_min) wr_min <= cyc1 - wr_start;
            if (cyc1 - wr_start > wr_max) wr_max <= cyc1 - wr_start;
            wr_hist[cyc1 - wr_start > 63 ? 63 : cyc1 - wr_start] <= wr_hist[cyc1 - wr_start > 63 ? 63 : cyc1 - wr_start] + 1;
        end
    end
    integer d_inflight = 0, d_wait;
    // one access, raised gap clk1x after the clock the previous one's done was seen on
    task dreq(input integer gap, input rnw, input [18:0] adr, input [15:0] d, input isv, input [4:0] voice);
        reg [26:0] full; reg [15:0] want;
        begin
            full = 27'h1000000 + {8'b0, adr};
            repeat (gap) @(posedge clk1);
            spu_req <= 1; spu_rnw <= rnw; spu_addr <= adr; spu_data <= {16'h0, d}; spu_be <= 4'b0011;
            spu_isvoice <= isv; spu_voice <= voice;
            if (wr_landed < wr_issued) d_inflight = d_inflight + 1;   // the write before has not reached SDRAM
            if (!rnw) begin gold[gi(full)] = d; wr_issued = wr_issued + 1; end
            want = gold[gi(full)];
            @(posedge clk1); spu_req <= 0; spu_isvoice <= 0;
            d_wait = 0;
            while (!spu_done) begin
                @(posedge clk1); d_wait = d_wait + 1;
                if (d_wait > 4000) begin $display("ERROR no answer, clk1x %0d addr %h", cyc1, full); $finish; end
            end
            if (rnw && spu_q[15:0] !== want) bad(spu_q, want, full);
        end
    endtask
    integer d_iter = 0, d_e0, d_i0, dc, dg, dg2, dcached, dkind, dn;
    integer d_runs [0:3], d_errs [0:3], d_infl [0:3];
    reg [18:0] dX, dY, dL; reg [4:0] dv, dw; reg [2:0] dh;
    task dcase(input integer c, input integer g, input integer g2, input integer cached, input integer kind, input integer n);
        begin
            dL = 19'h02000 + (d_iter % 3328) * 16; dh = d_iter; dX = dL + {dh, 1'b0};
            dv = d_iter % 24; dw = (d_iter + 7) % 24;
            d_e0 = errors; d_i0 = d_inflight;
            case (c)
                0: begin
                    if (cached) dreq(1, 1, dX, 0, kind == 0, dv);
                    dreq(1, 0, dX, $random(s2), 0, 0);
                    dreq(g, 1, dX, 0, kind == 0, dv);
                end
                1: begin
                    dY = dL + {dh + n[2:0], 1'b0};
                    if (cached) begin dreq(1, 1, dX, 0, kind == 0, dv); dreq(1, 1, dY, 0, kind == 0, dv); end
                    dreq(1, 0, dX, $random(s2), 0, 0);
                    dreq(g, 1, dY, 0, kind == 0, dv);
                    dreq(1, 1, dX, 0, kind == 0, dv);
                end
                2: begin
                    dY = (dL ^ 19'h00200) + {dh + 3'd3, 1'b0};
                    if (cached) begin dreq(1, 1, dX, 0, kind == 0, dv); dreq(1, 1, dY, 0, 1, dw); end
                    dreq(1, 0, dX, $random(s2), 0, 0);
                    dreq(g, 1, dY, 0, 1, dw);
                    dreq(1, 0, dY, $random(s2), 0, 0);
                    dreq(g, 1, dY, 0, 1, dw);
                    dreq(1, 1, dX, 0, kind == 0, dv);
                end
                default: begin
                    if (cached) dreq(1, 1, dX, 0, kind == 0, dv);
                    dreq(1, 0, dX, $random(s2), 0, 0);
                    dreq(g, 0, dX, $random(s2), 0, 0);
                    dreq(g2, 1, dX, 0, kind == 0, dv);
                end
            endcase
            d_runs[c] = d_runs[c] + 1; d_errs[c] = d_errs[c] + errors - d_e0; d_infl[c] = d_infl[c] + d_inflight - d_i0;
            d_iter = d_iter + 1;
        end
    endtask
    task directed;
        begin
            for (dc = 0; dc < 4; dc = dc + 1) begin d_runs[dc] = 0; d_errs[dc] = 0; d_infl[dc] = 0; end
            for (dcached = 0; dcached < 2; dcached = dcached + 1) for (dkind = 0; dkind < 2; dkind = dkind + 1)
                for (dg = 0; dg <= GAPS; dg = dg + 1) begin
                    dcase(0, dg, 0, dcached, dkind, 0);
                    for (dn = 1; dn < 8; dn = dn + 1) dcase(1, dg, 0, dcached, dkind, dn);
                    dcase(2, dg, 0, dcached, dkind, 0);
                    for (dg2 = 0; dg2 <= GAPS; dg2 = dg2 + 1) dcase(3, dg, dg2, dcached, dkind, 0);
                end
            d_e0 = errors;
            repeat (200) @(posedge clk1);
            for (di = 0; di < 3328 * 8; di = di + 1) dreq(1, 1, 19'h02000 + {di[15:0], 1'b0}, 0, 0, 0);   // what SDRAM holds now
            $display("DIRECTED case read-same runs %0d errors %0d inflight %0d", d_runs[0], d_errs[0], d_infl[0]);
            $display("DIRECTED case read-line runs %0d errors %0d inflight %0d", d_runs[1], d_errs[1], d_infl[1]);
            $display("DIRECTED case read-region runs %0d errors %0d inflight %0d", d_runs[2], d_errs[2], d_infl[2]);
            $display("DIRECTED case write-read runs %0d errors %0d inflight %0d", d_runs[3], d_errs[3], d_infl[3]);
            $display("DIRECTED case read-back runs %0d errors %0d inflight 0", 3328 * 8, errors - d_e0);
            $display("DIRECTED writes %0d landed %0d clk1x_min %0d clk1x_max %0d gaps %0d", wr_issued, wr_landed, wr_min, wr_max, GAPS);
            for (di = 0; di < 64; di = di + 1) if (wr_hist[di] != 0) $display("DIRECTED write_clk1x %0d count %0d", di, wr_hist[di]);
            $display("RESULT slot %0d cache %0d load %0d pitch %0d errors %0d late_ticks 0 mean 0 max 0 reads %0d hits %0d", SLOT, CACHE, LOAD, PITCH, errors, reads, hits);
        end
    endtask

    reg [18:0] xadr = 19'h0D000, xlast = 19'h0D000;
    reg [18:0] blk [0:23]; integer smp [0:23];
    initial begin
        for (v = 0; v < 24; v = v + 1) begin blk[v] = 19'h1000 + v * 19'h800; smp[v] = v; end   // separate voice streams (the chip model keeps 64 KiB per bank)
        // LAYOUT 1: odd voices on a block whose hashed set equals the even
        // voice's (the 32-set hash XORs 5-bit address groups: flipping bits 9 and 14 keeps it),
        // and voices 0 and 1 on the very same block (one sample, two voices)
        if (LAYOUT == 1) for (v = 1; v < 24; v = v + 2) blk[v] = v == 1 ? blk[0] : blk[v-1] ^ 19'h04200;
        if (LAYOUT == 2) for (v = 0; v < 24; v = v + 1) blk[v] = 19'h1000 + v * 19'h40;
        if (XMODE == 1) blk[23] = 19'h0D800;
        repeat (20) @(posedge clk1); init = 0;
        repeat (30000) @(posedge clk1);
        if (DIRECTED != 0) begin directed; $finish; end
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
                if (XMODE == 1 && v == 23 && blk[v] >= 19'h0E000) blk[v] = 19'h0D000;
                // SLOT>0: spu.vhd with useSDRAM='1' (no --spu-free-slot) holds
                // each voice until about SLOT clk1x after its start (RAM_WAIT).
                while (SLOT > 0 && cyc1 - vstart < SLOT) @(posedge clk1);
                if (v < XFER) begin
                    spu_voice_access(0, xadr, $random(s2), XMODE == 1, v[4:0]); xlast = xadr;
                    xadr = xadr + 19'd2; if (xadr >= 19'h0E000) xadr = 19'h0D000;
                end
            end
            if (XFER > 0) spu_access(1, xlast, 0);   // not a voice read: set 31 with voice sets
            // a voice's cached word written right after a read from a region
            // no voice plays and nothing writes (its owner entry stays empty)
            if (XFER > 0 && t % 4 == 1) begin
                v = t % 24;
                spu_voice_access(1, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, 0, 1, v[4:0]);
                spu_access(1, 19'h0F000 + {t[6:0], 1'b0}, 0);
                spu_access(0, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, $random(s2));
                spu_voice_access(1, blk[v] + 19'd2 + {smp[v][4:2], 1'b0}, 0, 1, v[4:0]);
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
