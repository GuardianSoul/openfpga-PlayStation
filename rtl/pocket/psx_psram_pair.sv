// SPDX-License-Identifier: MIT
// Normalized memory packets on the Pocket's two PSRAM chips as one 32-bit
// memory, using PocketQuake's hardware-proven psram.sv recipe (asynchronous
// writes, WAIT-gated synchronous burst reads, BCR 641F on both dies; passed on
// the test Pocket at 105 MHz, docs/pocket-sweep-probe.md). The controller is
// instantiated as psx_pq_psram: prepare_pocket_early.py --psram-main copies
// the pinned psram.sv with its module renamed (MIRLO64's is also "psram").
//
// Packet port (one clock, clk): halfword address, 1..8 halfwords, data and
// byte enables in halfword lanes, as psx_mem_cdc's destination side. Word w
// (halfwords 2w, 2w+1) lives at chip address w[21:0]: chip 0 holds the low
// halfword, chip 1 the high one. Halfword address bit 23 selects the die;
// bit 24 and bit 22 must be zero (main RAM, BIOS and SPU RAM fit:
// docs/memory-bandwidth.md, layout C). Reads are one burst on both chips;
// each 32-bit word also appears once, in order, on the fill stream before
// the response (the direct-fill SPU adapter uses it). Writes are one paired
// asynchronous write per word, touching only enabled halfwords. Every packet
// gets one response; rsp_error marks an address outside the map.
// PAIRED=0 uses chip 0 alone (bring-up fallback): halfword h at chip 0
// address h[21:0], word reads as one burst of twice the length, word writes
// as two halfword writes.
// chip_clk_en is high only while a burst read is in flight. The CellularRAM
// takes asynchronous writes in synchronous mode only with CLK held LOW, so a
// top that gates the chip clock with it (DDIO: high half = chip_clk_en, low
// half = 0) keeps writes from starting a burst write at the ADV# clock edge.
`default_nettype none
// WRITE_EXTRA adds clocks to each psram.sv write (patched copy: write states
// only; its other state numbers are fixed). WRITE_GAP idles that many clocks
// after a write packet before the next operation. BCR is written to both
// dies of both chips (641F: sync, fixed latency 4, half drive strength).
// LIVE_REQ=1: the requester holds req_wdata/req_byte_en from req_valid until
// the response is taken (psx_psram_clients does), so no copy is kept.
module psx_psram_pair #(parameter real CLOCK_MHZ=104.1667, parameter integer T_INIT=16000, parameter integer PAIRED=1,
    parameter integer WRITE_EXTRA=0, parameter integer WRITE_GAP=0, parameter [15:0] BCR=16'h641F,
    parameter integer LIVE_REQ=0) (
    input wire clk, reset,
    output reg initialized=0,
    input wire req_valid, req_write,
    output wire req_ready,
    input wire [24:0] req_addr,
    input wire [3:0] req_halfwords,
    input wire [127:0] req_wdata,
    input wire [15:0] req_byte_en,
    output wire rsp_valid,
    input wire rsp_ready,
    output wire [127:0] rsp_rdata,
    output reg rsp_error=0,
    output reg fill_valid=0,
    output reg [1:0] fill_word=0,
    output reg [31:0] fill_data=0,
    output wire idle,
    output wire chip_clk_en,
    output wire [3:0] dbg_state,   // sequencer state (diagnostic readout)
    // chip 0 and chip 1 pins (cramN_clk comes from a free-running PLL output)
    output wire [21:16] cram0_a, cram1_a,
    inout wire [15:0] cram0_dq, cram1_dq,
    input wire cram0_wait, cram1_wait,
    output wire cram0_adv_n, cram1_adv_n, cram0_cre, cram1_cre,
    output wire cram0_ce0_n, cram0_ce1_n, cram1_ce0_n, cram1_ce1_n,
    output wire cram0_oe_n, cram1_oe_n, cram0_we_n, cram1_we_n,
    output wire cram0_ub_n, cram0_lb_n, cram1_ub_n, cram1_lb_n
);
    // ---------------- the two chip controllers, in lockstep ----------------
    reg bank=0, write_en=0, burst_en=0, config_en=0;
    reg [21:0] chip_addr=0;
    reg [5:0] burst_len=0;
    reg [15:0] wdata0=0, wdata1=0;
    reg [1:0] be0=0, be1=0;
    reg we0=0, we1=0;
    wire [1:0] avail, busy;
    wire [15:0] rdata0, rdata1;
    psx_pq_psram #(.CLOCK_SPEED(CLOCK_MHZ), .WRITE_EXTRA(WRITE_EXTRA)) chip0 (.clk(clk), .bank_sel(bank), .addr(chip_addr),
        .write_en(write_en && we0), .data_in(wdata0), .write_high_byte(be0[1]), .write_low_byte(be0[0]),
        .read_en(1'b0), .sync_burst_en(burst_en), .sync_burst_len(burst_len),
        .config_en(config_en), .config_data(BCR),
        .read_avail(avail[0]), .data_out(rdata0), .busy(busy[0]),
        .dbg_wait_seen(), .dbg_wait_cycles(), .dbg_burst_count(), .dbg_stale_count(),
        .cram_a(cram0_a), .cram_dq(cram0_dq), .cram_wait(cram0_wait), .cram_clk(),
        .cram_adv_n(cram0_adv_n), .cram_cre(cram0_cre), .cram_ce0_n(cram0_ce0_n), .cram_ce1_n(cram0_ce1_n),
        .cram_oe_n(cram0_oe_n), .cram_we_n(cram0_we_n), .cram_ub_n(cram0_ub_n), .cram_lb_n(cram0_lb_n));
    psx_pq_psram #(.CLOCK_SPEED(CLOCK_MHZ), .WRITE_EXTRA(WRITE_EXTRA)) chip1 (.clk(clk), .bank_sel(bank), .addr(chip_addr),
        .write_en(write_en && we1 && PAIRED!=0), .data_in(wdata1), .write_high_byte(be1[1]), .write_low_byte(be1[0]),
        .read_en(1'b0), .sync_burst_en(burst_en && PAIRED!=0), .sync_burst_len(burst_len),
        .config_en(config_en), .config_data(BCR),
        .read_avail(avail[1]), .data_out(rdata1), .busy(busy[1]),
        .dbg_wait_seen(), .dbg_wait_cycles(), .dbg_burst_count(), .dbg_stale_count(),
        .cram_a(cram1_a), .cram_dq(cram1_dq), .cram_wait(cram1_wait), .cram_clk(),
        .cram_adv_n(cram1_adv_n), .cram_cre(cram1_cre), .cram_ce0_n(cram1_ce0_n), .cram_ce1_n(cram1_ce1_n),
        .cram_oe_n(cram1_oe_n), .cram_we_n(cram1_we_n), .cram_ub_n(cram1_ub_n), .cram_lb_n(cram1_lb_n));

    // ---------------- packet sequencer ----------------
    localparam [3:0] INIT=0, CFG0=1, CFG_WAIT=2, IDLE=3, WR=4, WR_WAIT=5, RD=6, RD_DATA=7, RSP=8, SETTLE=9, GAP=10;
    reg [3:0] state=INIT, after=INIT;
    reg [15:0] timer=0;
    reg write=0, odd=0;
    reg [21:0] first=0;
    reg [2:0] words=0, index=0;
    reg [3:0] halfwords=0;
    reg [127:0] wcopy=0;
    reg [15:0] becopy=0;
    wire [127:0] wbuf=LIVE_REQ!=0 ? req_wdata : wcopy;
    wire [15:0] bebuf=LIVE_REQ!=0 ? req_byte_en : becopy;
    reg [15:0] got0[0:4], got1[0:4];
    reg [3:0] n0=0, n1=0;
    reg [2:0] filled=0;
    reg half=0;
    // words with both halves present (registered: one cycle behind the
    // counters, which only grow, keeps the compare out of the state logic)
    reg [3:0] have=0;
    // Up to five words (eight halfwords from an odd start), read straight
    // from got0/got1: they change only in RD_DATA, and every word below
    // filled is complete there (rsp_words used to hold a second copy).
    wire [159:0] rsp_words;
    genvar w;
    generate for (w=0; w<5; w=w+1) begin : rsp_word
        assign rsp_words[w*32+:32]=w<filled ? {got1[w],got0[w]} : 32'b0;
    end endgenerate
    wire [159:0] rsp_shifted=odd ? {16'b0,rsp_words[159:16]} : rsp_words;
    assign rsp_rdata=rsp_shifted[127:0];
    always @(posedge clk) have<=state!=RD_DATA ? 4'd0 : PAIRED!=0 ? (n0<n1 ? n0 : n1) : {1'b0,n0[3:1]};
    assign req_ready=!reset && state==IDLE;
    assign rsp_valid=!reset && state==RSP;
    assign idle=state==IDLE && busy==2'b00;
    assign chip_clk_en=state==RD_DATA;
    assign dbg_state=state;

    wire [4:0] end_half={1'b0,req_halfwords}+{4'b0,req_addr[0]};
    // Analogue: the last word of a die (3FFFFFh) is the configuration-register
    // access path, so no packet may reach it (the map never does).
    wire [22:0] last_half={1'b0,req_addr[21:0]}+{19'b0,req_halfwords}-23'd1;
    wire bad=req_addr[24] || req_addr[22] || req_halfwords==0 || req_halfwords>8 || last_half[22:1]>=22'h3fffff;
    // packet halfword k of word slot i, lane l: k = 2i + l - odd
    function automatic [4:0] lane_index(input [2:0] i, input l, input o);
        lane_index={1'b0,i,l}-{4'b0,o};
    endfunction
    wire [4:0] k0=lane_index(index,1'b0,odd), k1=lane_index(index,1'b1,odd);
    wire in0=!k0[4] && k0[3:0]<halfwords, in1=!k1[4] && k1[3:0]<halfwords;
    wire [1:0] e0=in0 ? bebuf[k0[2:0]*2+:2] : 2'b00, e1=in1 ? bebuf[k1[2:0]*2+:2] : 2'b00;

    always @(posedge clk) begin
        write_en<=0; burst_en<=0; config_en<=0; fill_valid<=0;
        if (reset) begin
            state<=INIT; timer<=0; initialized<=0; rsp_error<=0; n0<=0; n1<=0; filled<=0;
        end else case (state)
            INIT: begin
                timer<=timer+1'b1;
                if (timer==T_INIT[15:0]) begin bank<=0; state<=CFG0; end
            end
            CFG0: begin config_en<=1; after<=bank ? IDLE : CFG0; state<=SETTLE; end
            SETTLE: state<=CFG_WAIT;            // busy rises the cycle after a start
            CFG_WAIT: if (busy==2'b00) begin
                if (after==IDLE) begin initialized<=1; state<=IDLE; end
                else begin bank<=1; state<=CFG0; end
            end
            IDLE: if (req_valid) begin
                write<=req_write; odd<=req_addr[0]; halfwords<=req_halfwords;
                first<=req_addr[22:1]; bank<=req_addr[23];
                words<=(end_half+5'd1)>>1;
                if (LIVE_REQ==0) begin wcopy<=req_wdata; becopy<=req_byte_en; end
                index<=0;
                n0<=0; n1<=0; filled<=0; half<=0; rsp_error<=bad;
                state<=bad ? RSP : req_write ? WR : RD;
            end
            WR: begin
                if (PAIRED!=0) begin
                    chip_addr<=first+{19'b0,index};
                    wdata0<=in0 ? wbuf[k0[2:0]*16+:16] : 16'h0; be0<=e0; we0<=e0!=0;
                    wdata1<=in1 ? wbuf[k1[2:0]*16+:16] : 16'h0; be1<=e1; we1<=e1!=0;
                end else begin
                    // chip 0 alone: low half, then high half at the next address
                    chip_addr<={first[20:0]+{18'b0,index},half};
                    wdata0<=half ? (in1 ? wbuf[k1[2:0]*16+:16] : 16'h0) : (in0 ? wbuf[k0[2:0]*16+:16] : 16'h0);
                    be0<=half ? e1 : e0; we0<=(half ? e1 : e0)!=0; we1<=0;
                end
                write_en<=1; state<=WR_WAIT; after<=WR;
            end
            WR_WAIT: if (!write_en && busy==2'b00) begin
                if (PAIRED==0 && !half) begin half<=1; state<=WR; end
                else if (index+1'b1==words) begin timer<=0; state<=WRITE_GAP!=0 ? GAP : RSP; end
                else begin index<=index+1'b1; half<=0; state<=WR; end
            end
            RD: begin
                chip_addr<=PAIRED!=0 ? first : {first[20:0],1'b0};
                burst_len<=PAIRED!=0 ? {3'b0,words}-6'd1 : {2'b0,words,1'b0}-6'd1;
                burst_en<=1; state<=RD_DATA;
            end
            RD_DATA: begin
                if (PAIRED!=0) begin
                    if (avail[0]) begin got0[n0[2:0]]<=rdata0; n0<=n0+1'b1; end
                    if (avail[1]) begin got1[n1[2:0]]<=rdata1; n1<=n1+1'b1; end
                end else if (avail[0]) begin
                    if (n0[0]) got1[n0[3:1]]<=rdata0; else got0[n0[3:1]]<=rdata0;
                    n0<=n0+1'b1;
                end
                if ({1'b0,filled}<have) begin
                    fill_valid<=1; fill_word<=filled[1:0]; fill_data<={got1[filled],got0[filled]};
                    filled<=filled+1'b1;
                end else if (filled==words) state<=RSP;
            end
            GAP: begin timer<=timer+1'b1; if (timer+1'b1>=WRITE_GAP[15:0]) state<=RSP; end
            RSP: if (rsp_ready) state<=IDLE;
            default: state<=INIT;
        endcase
    end
endmodule
`default_nettype wire
