// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// psx_vram_x3: carries psx_vram_psram's DDR and aux ports from clk2x (GPU,
// memory card slot) to a controller that runs on clk3x (fast clock, see the
// controller header), so the PSRAM chips can be clocked at 101.6 MHz.
//
// Clocks: clk2x (67.7 MHz) and clk3x (101.6 MHz) come from one PLL with
// aligned edges (2:3). Every crossing is a plain register-to-register path
// that STA times as a related-clock path (worst edge spacing 4.92 ns in both
// directions); no synchroniser, no false path, no multicycle. Values that
// cross are either sampled each clock and used one clock later, or held
// longer than one period of the receiving clock (see each port).
//
// DDR port (clk2x side has psx_vram_psram's contract)
//   Requests go through a 4-entry FIFO (storage and write pointer written on
//   clk2x; clk3x registers the write pointer, compares it and loads the
//   entry one clk3x later, at least 14.76 ns after the clk2x edge: the block
//   RAM holds the word only after the second half of that clk2x cycle) into a head register
//   on clk3x that drives the controller's request inputs; an entry leaves the head
//   when the controller takes it (!ddr_busy). ddr_busy (clk2x register) is
//   high while three or more entries may be used (the read pointer seen on
//   clk2x is one clock old) or while a read's words are still to come, so a
//   read is the only request in flight, as with the controller alone.
//   Writes stay posted: accept = done for the master.
//   Read words: the controller's ddr_dout_ready (clk3x, at most one per two
//   clk3x = 19.7 ns, one per beat pair) loads a hold register and flips a
//   toggle. clk2x samples both every clock (14.8 ns < 19.7 ns, so no word
//   is missed or seen twice) and raises ddr_dout_ready for one clock when
//   the toggle has changed, with the word in a clk2x register.
// Aux port (memory card slot / save states, clk2x side)
//   aux_req is held until the one-clock aux_done; the clock after aux_done
//   may carry the next request (psx_memcard_slot's back-to-back format
//   writes). aux_we/aux_addr/aux_din are held with it (psx_vram_psram reads
//   them live during the access, so every aux master already holds them).
//   A clk2x toggle hands the request over; clk3x copies aux_we/addr/din
//   straight into its own registers (they have been stable since the
//   request edge) and holds the controller's aux_req until aux_done.
//   aux_done returns by a toggle.
//   aux_dout is the controller's register, held until the next aux read.
// card_write returns by a toggle (one clk2x pulse per controller pulse).
//
// reset keeps the controller's contract: an accepted write is completed
// across reset, an accepted read is dropped (no ddr_dout_ready for it).
// ddr_busy is high during reset, so nothing is pushed then. Each FIFO entry
// carries the reset epoch it was pushed in (a clk2x bit flipped at every
// reset); clk3x drops a read from an older epoch (in the head or as it
// leaves the FIFO) and passes writes on. The toggles clear.

`default_nettype none

module psx_vram_x3 (
    input  wire        clk2x,
    input  wire        clk3x,
    input  wire        reset,

    // clk2x: GPU (MiSTer DDR3 port)
    output reg         ddr_busy = 1'b1,
    input  wire  [7:0] ddr_burstcnt,
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [24:0] ddr_addr,          // [24:22] alias (psx_vram_psram ignores them)
    /* verilator lint_on UNUSEDSIGNAL */
    output reg  [63:0] ddr_dout = 64'd0,
    output wire        ddr_dout_ready,
    input  wire        ddr_rd,
    input  wire [63:0] ddr_din,
    input  wire  [7:0] ddr_be,
    input  wire        ddr_we,
    // clk2x: aux
    input  wire        aux_req,
    input  wire        aux_we,
    input  wire [20:0] aux_addr,
    input  wire [31:0] aux_din,
    output reg         aux_done = 1'b0,
    output reg         card_write = 1'b0,

    // clk3x: psx_vram_psram
    input  wire        c_busy,
    output wire  [7:0] c_burstcnt,
    output wire [24:0] c_addr,
    input  wire [63:0] c_dout,
    input  wire        c_dout_ready,
    output wire        c_rd,
    output wire [63:0] c_din,
    output wire  [7:0] c_be,
    output wire        c_we,
    output wire        c_aux_req,
    output reg         c_aux_we = 1'b0,
    output reg  [20:0] c_aux_addr = 21'd0,
    output reg  [31:0] c_aux_din = 32'd0,
    input  wire        c_aux_done,
    input  wire        c_card_write
);

// ---- request FIFO: {burstcnt, addr[21:0], be, din} in block RAM; we and the
// reset epoch in registers beside it, so the head's load enable never depends
// on RAM data (that would make the RAM read asynchronous: 416 flip-flops)
localparam int W = 8 + 22 + 8 + 64;
reg  [W-1:0] mem [0:3];
(* ramstyle = "logic" *) reg [3:0] weq = 4'd0;   // Quartus still merges weq/epq into a small M10K (same read timing as mem)
(* ramstyle = "logic" *) reg [3:0] epq = 4'd0;
reg  [2:0]   wptr = 3'd0;          // clk2x
reg  [2:0]   rptr = 3'd0;          // clk3x
reg  [2:0]   rptr_2x = 3'd0;       // clk3x rptr sampled on clk2x
reg  [7:0]   rd_left = 8'd0;       // clk2x: read words still to come
reg          ep = 1'b0, rst_d = 1'b0;   // clk2x: reset epoch

wire push = (ddr_rd || ddr_we) && !ddr_busy;
wire [2:0] wptr_n = wptr + {2'd0, push};
wire [7:0] rd_left_n = push && !ddr_we ? ddr_burstcnt : rd_left - {7'd0, ddr_dout_ready && rd_left != 8'd0};

always @(posedge clk2x) begin
    rptr_2x <= rptr;
    rst_d <= reset;
    if (reset && !rst_d) ep <= !ep;
    if (push) begin
        weq[wptr[1:0]] <= ddr_we; epq[wptr[1:0]] <= ep;
    end
    wptr <= wptr_n;                   // also in reset: ddr_busy is a register, so the first reset clock can push
    if (reset) begin
        rd_left <= 8'd0; ddr_busy <= 1'b1;
    end else begin
        rd_left <= rd_left_n;
        ddr_busy <= (wptr_n - rptr_2x) >= 3'd3 || rd_left_n != 8'd0;
    end
end

// The RAM write. An M10K stores it in the second half of the clk2x cycle
// (STA does not time a write against the other port's read), so the
// benches model that with PSX_M10K_WRITE_HALF.
wire [W-1:0] wword = {ddr_we ? 8'd1 : ddr_burstcnt, ddr_addr[21:0], ddr_be, ddr_din};
`ifdef PSX_M10K_WRITE_HALF
reg [W-1:0] wd = {W{1'b0}};
reg [1:0]   wa = 2'd0;
reg         wv = 1'b0;
always @(posedge clk2x) begin wv <= push; wa <= wptr[1:0]; wd <= wword; end
always @(negedge clk2x) if (wv) mem[wa] <= wd;
`else
always @(posedge clk2x) if (push) mem[wptr[1:0]] <= wword;
`endif

// ---- head register on clk3x
reg [W-1:0] head = {W{1'b0}};
reg         hv = 1'b0;
// The write pointer is registered on clk3x before the compare, so an entry
// is read at least one clk3x period after the clk3x edge that first sees
// it: >= 14.76 ns after its clk2x write edge, past the RAM's write (the
// entry's RAM word is not valid at that first edge).
reg  [2:0]  wptr_s = 3'd0;
wire        have = wptr_s != rptr;
reg         ep_s = 1'b0;             // clk3x copy of the epoch

reg         h_ep = 1'b0, h_we = 1'b0;
assign {c_burstcnt, c_addr[21:0], c_be, c_din} = head;
assign c_addr[24:22] = 3'd0;
wire        h_old = !h_we && h_ep != ep_s;   // a read from before a reset: dropped
wire        take = hv && (h_old || !c_busy);      // a stale read just leaves
assign c_rd = hv && !h_we && !h_old;
assign c_we = hv && h_we;
always @(posedge clk3x) begin
    ep_s <= ep;
    wptr_s <= wptr;                  // clk2x wptr into clk3x (timed 4.92 ns)
    if (!hv || take) begin
        hv <= have;
        if (have) begin
            head <= mem[rptr[1:0]]; h_we <= weq[rptr[1:0]]; h_ep <= epq[rptr[1:0]];
            rptr <= rptr + 3'd1;
        end
    end
end

// ---- read words back to clk2x
reg [63:0] rhold = 64'd0;
reg        rtog = 1'b0, rtog_s = 1'b0, rtog_d = 1'b0;
always @(posedge clk3x) begin
    if (reset) rtog <= 1'b0;
    else if (c_dout_ready) begin rhold <= c_dout; rtog <= !rtog; end
end
always @(posedge clk2x) begin
    rtog_s <= reset ? 1'b0 : rtog;
    rtog_d <= reset ? 1'b0 : rtog_s;
    ddr_dout <= rhold;
end
assign ddr_dout_ready = rtog_s != rtog_d;

// ---- aux: clk2x request registers and toggle
reg        a_tog = 1'b0, a_busy = 1'b0;
reg        d_tog = 1'b0, d_tog_s = 1'b0, d_seen = 1'b0;   // aux_done toggle (clk3x) and its clk2x copies
reg        w_tog = 1'b0, w_tog_s = 1'b0, w_seen = 1'b0;   // card_write toggle
always @(posedge clk2x) begin
    d_tog_s <= d_tog; w_tog_s <= w_tog;
    aux_done <= 1'b0; card_write <= 1'b0;
    if (reset) begin
        a_tog <= 1'b0; a_busy <= 1'b0; d_seen <= 1'b0; w_seen <= 1'b0; d_tog_s <= 1'b0; w_tog_s <= 1'b0;
    end else begin
        if (aux_req && !a_busy && !aux_done) begin
            a_tog <= !a_tog; a_busy <= 1'b1;
        end
        if (d_tog_s != d_seen) begin d_seen <= d_tog_s; aux_done <= 1'b1; a_busy <= 1'b0; end
        if (w_tog_s != w_seen) begin w_seen <= w_tog_s; card_write <= 1'b1; end
    end
end

// ---- aux: clk3x side
reg a_tog_s = 1'b0, a_seen = 1'b0, a_on = 1'b0;
assign c_aux_req = a_on;
always @(posedge clk3x) begin
    a_tog_s <= a_tog;
    if (reset) begin
        a_tog_s <= 1'b0; a_seen <= 1'b0; a_on <= 1'b0; d_tog <= 1'b0; w_tog <= 1'b0;
    end else begin
        if (c_card_write) w_tog <= !w_tog;
        if (a_on) begin
            if (c_aux_done) begin a_on <= 1'b0; d_tog <= !d_tog; end
        end else if (a_tog_s != a_seen) begin
            a_seen <= a_tog_s; a_on <= 1'b1;
            c_aux_we <= aux_we; c_aux_addr <= aux_addr; c_aux_din <= aux_din;
        end
    end
end

endmodule

`default_nettype wire
