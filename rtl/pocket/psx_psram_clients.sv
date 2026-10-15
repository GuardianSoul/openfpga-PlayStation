// SPDX-License-Identifier: MIT
// Three packet clients (0 main RAM + BIOS, 1 SPU RAM, 2 BIOS loader) on the
// paired PSRAM backend (psx_psram_pair). One packet at a time: a client is
// granted when nothing is in flight, keeps the grant until its response is
// taken, and the next grant starts from the following client (round robin).
// The fill stream goes to client 1 (the direct-fill SPU adapter) and, as
// main_fill_valid, to client 0 (sync adapter cache-line streaming).
`default_nettype none
module psx_psram_clients #(parameter real CLOCK_MHZ=104.1667, parameter integer T_INIT=16000, parameter integer PAIRED=1,
    parameter integer WRITE_EXTRA=0, parameter integer WRITE_GAP=0, parameter [15:0] BCR=16'h641F) (
    input wire clk, reset,
    output wire initialized, idle,
    output wire chip_clk_en,      // psx_psram_pair: gate the chip clock with it
    output wire [7:0] debug,      // {pair state, busy, held, offer, pair idle}
    input wire [2:0] p_valid, p_write,
    output wire [2:0] p_ready,
    input wire [2:0][24:0] p_addr,
    input wire [2:0][3:0] p_halfwords,
    input wire [2:0][127:0] p_wdata,
    input wire [2:0][15:0] p_byte_en,
    output wire [2:0] r_valid, r_error,
    input wire [2:0] r_ready,
    output wire [127:0] r_rdata,
    output wire fill_valid,
    output wire main_fill_valid,  // the same stream for client 0 (main RAM cache fills)
    output wire [1:0] fill_word,
    output wire [31:0] fill_data,
    output wire [21:16] cram0_a, cram1_a,
    inout wire [15:0] cram0_dq, cram1_dq,
    input wire cram0_wait, cram1_wait,
    output wire cram0_adv_n, cram1_adv_n, cram0_cre, cram1_cre,
    output wire cram0_ce0_n, cram0_ce1_n, cram1_ce0_n, cram1_ce1_n,
    output wire cram0_oe_n, cram1_oe_n, cram0_we_n, cram1_we_n,
    output wire cram0_ub_n, cram0_lb_n, cram1_ub_n, cram1_lb_n
);
    // h_* change only on a capture, never while busy: the pair reads them
    // live (LIVE_REQ) for the whole packet.
    // A client is picked one cycle, its p_ready (a register: the round-robin
    // pick stays out of the clients' handshake logic, 104 MHz timing) rises
    // the next, and its packet is copied into registers on that handshake,
    // then offered to the backend: the client mux stays out of the backend's
    // request path. A client that drops p_valid before the handshake is
    // skipped.
    reg busy=0, held=0, h_write=0;
    reg [2:0] offer=0;
    reg [1:0] owner=0, last=2;
    reg [24:0] h_addr=0; reg [3:0] h_halfwords=0; reg [127:0] h_wdata=0; reg [15:0] h_byte_en=0;
    // (last+1)%3 and (last+2)%3 without dividers (last is 0..2)
    wire [1:0] next1=last==2'd2 ? 2'd0 : last+2'd1, next2=last==2'd0 ? 2'd2 : last-2'd1;
    wire [1:0] pick=p_valid[next1] ? next1 : p_valid[next2] ? next2 : last;
    wire grant=!reset && !busy && p_valid[pick];
    wire req_ready, rsp_valid, rsp_error, pair_fill, pair_idle;
    wire [3:0] pair_state;
    assign debug={pair_state, busy, held, offer!=0, pair_idle};
    // Idle only with no packet granted, held or in flight (Codex review).
    assign idle=!reset && !busy && !held && pair_idle;
    always @(posedge clk) begin
        if (reset) begin busy<=0; held<=0; last<=2; offer<=0; end
        else if (grant) begin
            busy<=1; owner<=pick; last<=pick; offer<=3'b001<<pick;
        end else if (offer!=0) begin
            offer<=0;
            if (p_valid[owner]) held<=1; else busy<=0;
        end else begin
            if (held && req_ready) held<=0;
            if (busy && !held && rsp_valid && r_ready[owner]) busy<=0;
        end
    end
    // The offered packet is copied on its handshake cycle. offer is a
    // register (a grant cannot coincide: offer implies busy), and the copy
    // is taken regardless of p_valid (a dropped packet's copy is never used),
    // so neither the round-robin pick nor the client's valid reaches the
    // 160-bit capture enable (104 MHz timing in full fits).
    always @(posedge clk) if (offer!=0) begin
        h_write<=p_write[owner]; h_addr<=p_addr[owner]; h_halfwords<=p_halfwords[owner];
        h_wdata<=p_wdata[owner]; h_byte_en<=p_byte_en[owner];
    end
    assign p_ready=offer & p_valid;
    assign r_valid=(busy && !held && rsp_valid) ? (3'b001<<owner) : 3'b000;
    assign r_error={3{rsp_error}};
    assign fill_valid=pair_fill && busy && owner==2'd1;
    assign main_fill_valid=pair_fill && busy && owner==2'd0;
    psx_psram_pair #(.CLOCK_MHZ(CLOCK_MHZ), .T_INIT(T_INIT), .PAIRED(PAIRED), .WRITE_EXTRA(WRITE_EXTRA), .WRITE_GAP(WRITE_GAP), .BCR(BCR), .LIVE_REQ(1)) pair (.clk(clk), .reset(reset), .initialized(initialized),
        .req_valid(held), .req_write(h_write), .req_ready(req_ready), .req_addr(h_addr),
        .req_halfwords(h_halfwords), .req_wdata(h_wdata), .req_byte_en(h_byte_en),
        .rsp_valid(rsp_valid), .rsp_ready(busy && !held && r_ready[owner]), .rsp_rdata(r_rdata), .rsp_error(rsp_error),
        .fill_valid(pair_fill), .fill_word(fill_word), .fill_data(fill_data), .idle(pair_idle), .chip_clk_en(chip_clk_en), .dbg_state(pair_state),
        .cram0_a(cram0_a), .cram1_a(cram1_a), .cram0_dq(cram0_dq), .cram1_dq(cram1_dq),
        .cram0_wait(cram0_wait), .cram1_wait(cram1_wait), .cram0_adv_n(cram0_adv_n), .cram1_adv_n(cram1_adv_n),
        .cram0_cre(cram0_cre), .cram1_cre(cram1_cre), .cram0_ce0_n(cram0_ce0_n), .cram0_ce1_n(cram0_ce1_n),
        .cram1_ce0_n(cram1_ce0_n), .cram1_ce1_n(cram1_ce1_n), .cram0_oe_n(cram0_oe_n), .cram1_oe_n(cram1_oe_n),
        .cram0_we_n(cram0_we_n), .cram1_we_n(cram1_we_n), .cram0_ub_n(cram0_ub_n), .cram0_lb_n(cram0_lb_n),
        .cram1_ub_n(cram1_ub_n), .cram1_lb_n(cram1_lb_n));
endmodule
`default_nettype wire
