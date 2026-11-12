// SPDX-License-Identifier: GPL-2.0-only
// Main RAM, BIOS and SPU RAM of the MiSTer PSX core on the Pocket's one SDRAM
// chip. Wiring after MiSTer PSX.sv (Robert Peip, GPL-2.0): the core's main RAM
// port goes to an SDRAM controller with MiSTer sdram.sv's ports, reads on
// channel 1 and writes on channel 2 (done = either channel ready), with the
// cache, DMA and DMA-FIFO side ports passed straight through. MiSTer puts SPU
// RAM on a second SDRAM; here it shares the chip on channel 3 at SPU_BASE.
//
// Byte map (MiSTer's): main RAM at 0, BIOS at BIOS_BASE (0x800000), SPU RAM
// at SPU_BASE (0x1000000).
//
// BIOS loader (our packet interface, see psx_bios_patch and core_top c_*):
// while cpu_reset, channel 3 belongs to the loader instead of SPU RAM. A
// packet of 1-4 whole words (load_halfwords 2/4/6/8) at a halfword address
// inside the BIOS is taken when idle, its words are written one at a time
// (one one-clock request each, the next after channel 3's ready), then
// load_rsp is raised until load_rsp_ready. spuram_done stays low while
// cpu_reset.
//
// All ports are clk1x except the controller's own clock (clk3x); the ch3
// read data register is on the falling clk1x edge.
`default_nettype none
module psx_core_memory #(
    parameter [26:0] BIOS_BASE = 27'h0800000,
    parameter [26:0] SPU_BASE  = 27'h1000000
) (
    input  wire        clk1x,
    input  wire        clk3x,
    input  wire        init,          // SDRAM reset (PLL not locked)
    input  wire        cpu_reset,     // core held in reset: channel 3 is the loader's

    // core main RAM port (MiSTer psx_top)
    input  wire        ram_refresh,
    input  wire        ram_ena,
    input  wire        ram_rnw,
    input  wire        ram_dma,
    input  wire        ram_cache,
    input  wire [24:0] ram_adr,
    input  wire  [1:0] ram_cntDMA,
    input  wire [31:0] ram_dataWrite,
    input  wire  [3:0] ram_be,
    output wire        ram_done,
    output wire [31:0] ram_dataRead32,
    output wire        dma_wr,
    output wire [31:0] dma_data,
    output wire        dma_reqprocessed,
    output wire  [3:0] cache_wr,
    output wire [31:0] cache_data,
    output wire  [7:0] cache_addr,
    input  wire [22:0] ram_dmafifo_adr,
    input  wire [31:0] ram_dmafifo_data,
    input  wire        ram_dmafifo_empty,
    output wire        ram_dmafifo_read,

    // core SPU RAM port
    input wire spuram_ena, spuram_rnw,
    input  wire [18:0] spuram_adr,
    input  wire [31:0] spuram_dataWrite,
    input  wire  [3:0] spuram_be,
    output wire        spuram_done,
    output wire [31:0] spuram_dataRead,

    // BIOS loader packets
    input  wire         load_valid,
    input  wire  [24:0] load_addr,       // halfword address inside the BIOS
    input  wire   [3:0] load_halfwords,  // 2, 4, 6 or 8
    input  wire [127:0] load_data,       // word 0 in bits 31:0
    input  wire  [15:0] load_be,         // word 0 in bits 3:0
    output wire         load_ready,
    output reg          load_rsp = 1'b0,
    input  wire         load_rsp_ready,

    // SDRAM pins
    inout  wire [15:0] dram_dq,
    output wire [12:0] dram_a,
    output wire  [1:0] dram_dqm,
    output wire  [1:0] dram_ba,
    output wire        dram_we_n, dram_ras_n, dram_cas_n, dram_cke,
    output wire        dram_clk
);
    // ---------------------------------------------------------------- loader
    // ld_busy: a packet is being written or answered. ld_left counts the
    // words still to request after the one in flight.
    reg          ld_busy = 1'b0;
    reg          ld_req = 1'b0;
    reg   [1:0]  ld_left = 2'd0;
    reg  [26:0]  ld_addr = 27'd0;
    reg  [95:0]  ld_rest = 96'd0;     // words not yet requested, next in bits 31:0
    reg  [11:0]  ld_rest_be = 12'd0;
    reg  [31:0]  ld_word = 32'd0;
    reg   [3:0]  ld_word_be = 4'd0;
    wire         ld_ready_ch3;

    assign load_ready = cpu_reset && !ld_busy;

    always @(posedge clk1x) begin
        ld_req <= 1'b0;
        if (init) begin
            ld_busy <= 1'b0;
            load_rsp <= 1'b0;
        end else if (!ld_busy) begin
            if (load_valid && load_ready) begin
                ld_busy    <= 1'b1;
                ld_req     <= 1'b1;
                ld_addr    <= BIOS_BASE + {1'b0, load_addr, 1'b0};
                ld_word    <= load_data[31:0];
                ld_word_be <= load_be[3:0];
                ld_rest    <= load_data[127:32];
                ld_rest_be <= load_be[15:4];
                ld_left    <= load_halfwords[3:1] - 3'd1;
            end
        end else if (load_rsp) begin
            if (load_rsp_ready) begin
                load_rsp <= 1'b0;
                ld_busy  <= 1'b0;
            end
        end else if (ld_ready_ch3) begin
            if (ld_left != 2'd0) begin
                ld_req     <= 1'b1;
                ld_left    <= ld_left - 2'd1;
                ld_addr    <= ld_addr + 27'd4;
                ld_word    <= ld_rest[31:0];
                ld_word_be <= ld_rest_be[3:0];
                ld_rest    <= {32'd0, ld_rest[95:32]};
                ld_rest_be <= {4'd0, ld_rest_be[11:4]};
            end else begin
                load_rsp <= 1'b1;
            end
        end
    end

    // ------------------------------------------------------------- channel 3
    // SPU RAM (or the loader while cpu_reset) on channel 3.
    wire [26:0] ch3_addr;
    wire [31:0] ch3_din, ch3_dout_sdram;
    wire  [3:0] ch3_be;
    wire        ch3_req, ch3_rnw, ch3_ready;

    assign ld_ready_ch3 = ch3_ready && cpu_reset;

    // ch3 read data into the clk1x domain on the falling clk1x edge. The
    // controller loads ch3_dout_sdram (clk3x) at least four clk3x before
    // ch3_ready reaches clk1x and holds it until the next ch3 read, so the
    // word is already here at the falling edge before the rising edge that
    // takes it: same clk1x edge, same data. Both clk3x -> negedge clk1x and
    // negedge -> posedge clk1x get half a period of hold instead of zero.
    reg  [31:0] ch3_dout = 32'd0;
    always @(negedge clk1x) ch3_dout <= ch3_dout_sdram;

    // SPU RAM direct on channel 3 (prepare --spu-cache replaces this block)
    wire [26:0] spu_ch3_addr = SPU_BASE + {8'd0, spuram_adr};
    wire        spu_ch3_req  = spuram_ena;
    wire        spu_ch3_rnw  = spuram_rnw;
    wire [31:0] spu_ch3_din  = spuram_dataWrite;
    wire  [3:0] spu_ch3_be   = spuram_be;
    assign spuram_dataRead = ch3_dout;
    assign spuram_done     = ch3_ready && !cpu_reset;
    // end of the SPU RAM block

    assign ch3_addr = cpu_reset ? ld_addr    : spu_ch3_addr;
    assign ch3_din  = cpu_reset ? ld_word    : spu_ch3_din;
    assign ch3_be   = cpu_reset ? ld_word_be : spu_ch3_be;
    assign ch3_req  = cpu_reset ? ld_req     : spu_ch3_req;
    assign ch3_rnw  = cpu_reset ? 1'b0       : spu_ch3_rnw;

    // ------------------------------------------------------------ controller
    wire ch1_ready, ch2_ready;
    assign ram_done = ch1_ready || ch2_ready;

    sdram #(.CS_TIED_LOW(1)) controller (
        .init(init), .clk(clk3x), .clk_base(clk1x), .SDRAM_EN(1'b1),
        .SDRAM_DQ(dram_dq), .SDRAM_A(dram_a), .SDRAM_DQML(dram_dqm[0]), .SDRAM_DQMH(dram_dqm[1]),
        .SDRAM_BA(dram_ba), .SDRAM_nCS(), .SDRAM_nWE(dram_we_n), .SDRAM_nRAS(dram_ras_n),
        .SDRAM_nCAS(dram_cas_n), .SDRAM_CKE(dram_cke), .SDRAM_CLK(dram_clk),
        .refreshForce(ram_refresh), .ram_idle(),
        // channel 1: main RAM reads (single word, 4-word cache line, DMA burst)
        .ch1_addr({2'b00, ram_adr}), .ch1_dout(), .ch1_dout32(ram_dataRead32), .ch1_din(16'd0),
        .ch1_req(ram_ena && ram_rnw), .ch1_rnw(1'b1), .ch1_dma(ram_dma), .ch1_cntDMA(ram_cntDMA),
        .ch1_cache(ram_cache), .ch1_ready(ch1_ready),
        .cache_wr(cache_wr), .cache_data(cache_data), .cache_addr(cache_addr),
        .dma_wr(dma_wr), .dma_reqprocessed(dma_reqprocessed), .dma_data(dma_data),
        // channel 2: main RAM writes
        .ch2_addr({2'b00, ram_adr}), .ch2_dout(), .ch2_din(ram_dataWrite),
        .ch2_req(ram_ena && !ram_rnw), .ch2_rnw(1'b0), .ch2_be(ram_be), .ch2_ready(ch2_ready),
        // channel 3: SPU RAM, or the BIOS loader while cpu_reset
        .ch3_addr(ch3_addr), .ch3_dout(ch3_dout_sdram), .ch3_din(ch3_din),
        .ch3_req(ch3_req), .ch3_rnw(ch3_rnw), .ch3_be(ch3_be), .ch3_ready(ch3_ready),
        // DMA write FIFO
        .dmafifo_adr({4'd0, ram_dmafifo_adr}), .dmafifo_data(ram_dmafifo_data),
        .dmafifo_empty(ram_dmafifo_empty), .dmafifo_read(ram_dmafifo_read));
endmodule
`default_nettype wire
