// SPDX-License-Identifier: MIT
// Main RAM, BIOS and SPU RAM on the Pocket SDRAM through MiSTer's own
// sdram.sv on clk3x, wired to the core's native ports as MiSTer's PSX.sv
// does: ch1 = core reads
// (scalar, cache line, DMA), ch2 = core writes, ch3 = BIOS loader while the
// CPU is held in reset, SPU RAM afterwards. No adapter or clock crossing on
// the CPU path: sdram.sv aligns its clk1x requests to clk3x itself.
// The sdram module is a patched copy (prepare_pocket_early.py).
// MiSTer byte map: main RAM 0, BIOS 0x800000, SPU RAM 0x1000000.
`default_nettype none
module psx_native_memory (
    input wire clk1x, clk3x, init,      // init: SDRAM reset (PLL not locked)
    input wire cpu_reset,               // clk1x: the core is held in reset (loader owns ch3)
    // core main RAM port (clk1x)
    input wire ram_refresh,
    input wire ram_ena, ram_rnw, ram_dma, ram_cache,
    input wire [24:0] ram_adr, input wire [1:0] ram_cntDMA,
    input wire [31:0] ram_dataWrite, input wire [3:0] ram_be,
    output wire ram_done, output wire [31:0] ram_dataRead32,
    output wire dma_wr, output wire [31:0] dma_data, output wire dma_reqprocessed,
    output wire [3:0] cache_wr, output wire [31:0] cache_data, output wire [7:0] cache_addr,
    input wire [22:0] ram_dmafifo_adr, input wire [31:0] ram_dmafifo_data,
    input wire ram_dmafifo_empty, output wire ram_dmafifo_read,
    // core SPU RAM port (clk1x)
    input wire spuram_ena, spuram_rnw,
    input wire [18:0] spuram_adr, input wire [31:0] spuram_dataWrite, input wire [3:0] spuram_be,
    output wire spuram_done, output wire [31:0] spuram_dataRead,
    // BIOS loader packet (clk1x side of its CDC): halfword address within
    // the BIOS, 2/4/6/8 halfwords (whole words; psx_bios_patch writes 4)
    input wire load_valid, input wire [24:0] load_addr, input wire [3:0] load_halfwords,
    input wire [127:0] load_data, input wire [15:0] load_be,
    output wire load_ready, output reg load_rsp=0, input wire load_rsp_ready,
    // SDRAM pins
    inout wire [15:0] dram_dq, output wire [12:0] dram_a, output wire [1:0] dram_dqm, output wire [1:0] dram_ba,
    output wire dram_we_n, dram_ras_n, dram_cas_n, dram_cke, dram_clk
);
    // Loader: take a packet, write its words one ch3_req pulse each (sdram.sv
    // takes one request per one-clock pulse), answer after the last ch3_ready.
    localparam L_IDLE=0, L_WAIT=1, L_RSP=2;
    reg [1:0] lstate=L_IDLE;
    reg l_req=0;
    reg [26:0] l_addr=0;
    reg [31:0] l_data=0;
    reg [3:0] l_be=0;
    reg [127:0] l_words=0;
    reg [15:0] l_bes=0;
    reg [1:0] l_left=0;   // words after the current one
    wire ch3_ready;
    wire [31:0] ch3_dout;
    assign load_ready=cpu_reset && lstate==L_IDLE;
    always @(posedge clk1x) begin
        l_req<=0;
        case (lstate)
            L_IDLE: if (load_valid && load_ready) begin
                l_addr<=27'h800000+{1'b0,load_addr,1'b0}; l_data<=load_data[31:0]; l_be<=load_be[3:0];
                l_words<=load_data>>32; l_bes<=load_be>>4; l_left<=load_halfwords[3:1]-1'b1;
                l_req<=1; lstate<=L_WAIT;
            end
            L_WAIT: if (ch3_ready) begin
                if (l_left!=0) begin
                    l_addr<=l_addr+27'd4; l_data<=l_words[31:0]; l_be<=l_bes[3:0];
                    l_words<=l_words>>32; l_bes<=l_bes>>4; l_left<=l_left-1'b1; l_req<=1;
                end else begin load_rsp<=1; lstate<=L_RSP; end
            end
            L_RSP: if (load_rsp_ready) begin load_rsp<=0; lstate<=L_IDLE; end
            default: lstate<=L_IDLE;
        endcase
    end
    wire [26:0] ch3_addr=cpu_reset ? l_addr : 27'h1000000+{8'b0,spuram_adr};
    wire [31:0] ch3_din=cpu_reset ? l_data : spuram_dataWrite;
    wire [3:0] ch3_be=cpu_reset ? l_be : spuram_be;
    wire ch3_req=cpu_reset ? l_req : spuram_ena;
    wire ch3_rnw=cpu_reset ? 1'b0 : spuram_rnw;
    assign spuram_done=ch3_ready && !cpu_reset;
    assign spuram_dataRead=ch3_dout;

    wire rd_ready, wr_ready;
    assign ram_done=rd_ready || wr_ready;
    sdram #(.SINGLE_CHIP(1)) controller (
        .SDRAM_DQ(dram_dq), .SDRAM_A(dram_a), .SDRAM_DQML(dram_dqm[0]), .SDRAM_DQMH(dram_dqm[1]),
        .SDRAM_BA(dram_ba), .SDRAM_nCS(), .SDRAM_nWE(dram_we_n), .SDRAM_nRAS(dram_ras_n),
        .SDRAM_nCAS(dram_cas_n), .SDRAM_CKE(dram_cke), .SDRAM_CLK(dram_clk), .SDRAM_EN(1'b1),
        .init(init), .clk(clk3x), .clk_base(clk1x), .refreshForce(ram_refresh), .ram_idle(),
        .ch1_addr({2'b0,ram_adr}), .ch1_dout(), .ch1_dout32(ram_dataRead32), .ch1_din(16'b0),
        .ch1_req(ram_ena && ram_rnw), .ch1_rnw(1'b1), .ch1_dma(ram_dma), .ch1_cntDMA(ram_cntDMA),
        .ch1_cache(ram_cache), .ch1_ready(rd_ready),
        .cache_wr(cache_wr), .cache_data(cache_data), .cache_addr(cache_addr),
        .dma_wr(dma_wr), .dma_reqprocessed(dma_reqprocessed), .dma_data(dma_data),
        .ch2_addr({2'b0,ram_adr}), .ch2_dout(), .ch2_din(ram_dataWrite), .ch2_req(ram_ena && !ram_rnw),
        .ch2_rnw(1'b0), .ch2_be(ram_be), .ch2_ready(wr_ready),
        .ch3_addr(ch3_addr), .ch3_dout(ch3_dout), .ch3_din(ch3_din), .ch3_req(ch3_req), .ch3_rnw(ch3_rnw),
        .ch3_be(ch3_be), .ch3_ready(ch3_ready),
        .dmafifo_adr({4'b0,ram_dmafifo_adr}), .dmafifo_data(ram_dmafifo_data),
        .dmafifo_empty(ram_dmafifo_empty), .dmafifo_read(ram_dmafifo_read));
endmodule
`default_nettype wire
