// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// psx_vram_psram: the MiSTer PSX core's DDR3 port (GPU VRAM, memory cards)
// on the Pocket's two AS1C8M16PL CellularRAM chips, in synchronous fixed-
// latency burst mode. Written from docs/clean-room-memory.md, PSX_MiSTer's
// psx_top.vhd / gpu.vhd / memcard.vhd / ddram.sv and the Alliance datasheet.
//
// Clock and pins
//   clk is clk2x (67.7376 MHz). The chip clock is clk/2 (33.8688 MHz) out of
//   a fabric register (cram*_clk): it rises at "R" edges of clk and falls at
//   "F" edges. Every other pin output is a register that changes only at F
//   edges, half a chip period (14.76 ns) before and after the chip's rising
//   edge, so setup (3 ns) and hold (2 ns) have ~11 ns of margin each against
//   clock/data output skew. Read data the chip launches at chip edge k
//   (tACLK <= 9 ns, held tKOH >= 2 ns past edge k+1) is captured at the R
//   edge that launches chip edge k+1: about 29.5 - (Tco + flight + 9) ns of
//   setup and (Tco + flight + 2) ns of hold, i.e. the middle of the window.
//   The clock runs only while every die is in synchronous mode: it is held
//   low through the power-up wait, configuration-register writes and park
//   (a free-running clock in asynchronous mode turned writes into bursts on
//   the Pocket, docs/overnight-decisions.md 2026-10-15 02:55).
//
// Address map (64-bit DDR word address ddr_addr = DDR3 byte address [27:3])
//   die  = ddr_addr[21]              (CE0# die 0, CE1# die 1; [24:22] alias)
//   word = ddr_addr[20:0]            -> chip halfword {word, beat}, beat 0/1
//   beat 0 carries DDR bits [31:0], beat 1 bits [63:32]
//   cram0 = low halfword of a beat  (beat 0: bits 15:0,  beat 1: 47:32)
//   cram1 = high halfword of a beat (beat 0: bits 31:16, beat 1: 63:48)
//   Every MiSTer window (VRAM 0x0000000, card 1 0x0100000, card 2 0x0200000,
//   SPU 0x0300000, mirror/24-bit/frames 0x0400000-0x07FFFFF) is on die 0.
//   aux_addr (32-bit word i of card 1) = DDR word 0x20000 + i/2, beat i[0].
//   SAVE_AUX: aux_addr[20] set is the save-state buffer instead, word
//   i = aux_addr[19:0] at DDR word 0x200000 + i/2 (die 1 from byte 0),
//   beat i[0].
//
// Accesses
//   One chip row is 128 halfwords = 64 DDR words; a read burst is split into
//   segments at row ends (CE# high one chip cycle between, which also bounds
//   CE# low to 132 chip cycles = 3.90 us < tCEM 4 us; ROW_LOG2 5 halves it).
//   A read segment's last beat is captured at the next chip edge, which the
//   chip also sees (CE# rises half a cycle later); at a row end that edge
//   starts a row crossing whose output is never used. A segment: slot 0 CE#,
//   ADV#, address (WE# low for a write); data slots from LATENCY+1. A write
//   is one segment of two beats; a following write to the next word in the
//   same row, offered while the current one finishes, continues the burst.
//   ddr_busy is low only in a clk cycle that ends at an F edge where a
//   request can start, so an accepted request goes to the pins on the same
//   edge. Writes are posted (accept = done for the master); every accepted
//   write is completed, also across reset.
//   Read: first word 11 clk after the accepting edge (LATENCY 3), then one
//   word per 4 clk inside a segment; 133 chip cycles per 64-word segment.
//
// Reset (synchronous, active high): no new segment starts except accepted
//   writes; a running read stops at the next word; no dout_ready/aux_done is
//   produced; then the 150 us wait and the chips are configured again.
// Configuration from either mode: a die in synchronous mode ignores an
//   asynchronous register write (no clock edge), and another core may leave
//   the chips synchronous with its own BCR (they keep it until power-off).
//   So every configuration writes each register twice: in the form for an
//   asynchronous die (clock stopped) and in the form for a synchronous one
//   (CRE, CE#, ADV# and WE# sampled at a chip clock edge, the value on A/DQ).
//   At start the asynchronous pass goes first (a power-on die is then
//   synchronous), at park the synchronous one (our dies are synchronous).
// Park (any clock domain, synchronised here; unconnected reads as 0): no new
//   DDR request is accepted, accepted work finishes, both dies' BCRs get the
//   power-on value 0x9D1F (asynchronous) and the clock stops. While parked the
//   aux port still works, with asynchronous single accesses (6-7 chip cycles,
//   clock stopped), so APF can load the card while the core is held in reset
//   and read it back at quit with the chips already in their power-on mode.
//   Released: RCR and BCR are written again and the clock restarts.
//
// Full rate (FULL_RATE=1): the chip clock runs at the clk frequency. The top
//   makes it from a phase-shifted copy of clk through a DDIO output (high
//   half = cram*_clk, which is then a clock enable, low half = 0), so every
//   clk edge is an "F" edge for the bursts: pins change at each clk edge and
//   the chip samples them one phase shift later. The pins are sampled into
//   dq_r at every clk edge (an input register) and a read word is taken from
//   dq_r one clk later, two clks after the slot that marks it (the phase
//   must put the chip's data window around the clk edge after the next one).
//   Use LATENCY 5 at 67.7 MHz (code * tCLK >= 60 ns). The power-up wait,
//   asynchronous configuration and parked accesses keep half-rate slots
//   (every other clk); synchronous configuration runs at full rate.
//
// The top must drive cram*_dq from cram*_dq_out when cram*_dq_oe is high.
// Pin outputs drive both chips from one register; give the cram pins
// FAST_OUTPUT_REGISTER / FAST_OUTPUT_ENABLE_REGISTER so the fitter
// duplicates them into each IOE.

`default_nettype none

module psx_vram_psram #(
    parameter int LATENCY   = 3,      // BCR[13:11] fixed latency code
    parameter int READ_ADJ  = 0,      // extra chip cycles before the first read sample (bring-up)
    parameter int WRITE_ADJ = 0,      // extra chip cycles before the first write beat (bring-up)
    parameter int CLK_KHZ   = 67738,  // clk frequency, for the power-up wait
    parameter int INIT_US   = 150,    // datasheet power-up time
    parameter int ROW_LOG2  = 6,      // DDR words per segment, log2 (row = 64)
    parameter int SAVE_AUX  = 0,      // aux_addr[20]: the save-state buffer on die 1
    parameter bit FULL_RATE = 1'b0    // chip clock = clk (see "Full rate" above); cram*_clk is then its enable
)(
    input  wire        clk,
    input  wire        reset,
    input  wire        park,

    output wire        ddr_busy,
    input  wire  [7:0] ddr_burstcnt,
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [24:0] ddr_addr,          // [24:22] alias (no MiSTer window uses them)
    /* verilator lint_on UNUSEDSIGNAL */
    output wire [63:0] ddr_dout,
    output reg         ddr_dout_ready = 1'b0,
    input  wire        ddr_rd,
    input  wire [63:0] ddr_din,
    input  wire  [7:0] ddr_be,
    input  wire        ddr_we,

    input  wire        aux_req,
    input  wire        aux_we,
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [20:0] aux_addr,          // [20:15] are zero (128 KiB card), or SAVE_AUX
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire [31:0] aux_din,
    output reg  [31:0] aux_dout = 32'd0,
    output reg         aux_done = 1'b0,
    output reg         card_write = 1'b0,

    output wire [21:16] cram0_a,
    output wire [15:0] cram0_dq_out,
    output wire        cram0_dq_oe,
    input  wire [15:0] cram0_dq_in,
    output wire        cram0_clk,
    output wire        cram0_adv_n,
    output wire        cram0_cre,
    output wire        cram0_ce0_n,
    output wire        cram0_ce1_n,
    output wire        cram0_oe_n,
    output wire        cram0_we_n,
    output wire        cram0_ub_n,
    output wire        cram0_lb_n,

    output wire [21:16] cram1_a,
    output wire [15:0] cram1_dq_out,
    output wire        cram1_dq_oe,
    input  wire [15:0] cram1_dq_in,
    output wire        cram1_clk,
    output wire        cram1_adv_n,
    output wire        cram1_cre,
    output wire        cram1_ce0_n,
    output wire        cram1_ce1_n,
    output wire        cram1_oe_n,
    output wire        cram1_we_n,
    output wire        cram1_ub_n,
    output wire        cram1_lb_n
);

localparam int RD_FIRST   = LATENCY + 1 + READ_ADJ;   // first read sample slot
localparam int WR_FIRST   = LATENCY + 1 + WRITE_ADJ;  // first write beat slot
localparam int OE_SLOT    = LATENCY > 2 ? 2 : 1;
localparam int INIT_SLOTS = INIT_US * CLK_KHZ / 2000 + 16;
localparam logic [15:0] BCR_SYNC  = {1'b0, 1'b1, 3'(LATENCY), 1'b1, 1'b0, 1'b1, 2'b00, 2'b01, 1'b1, 3'b111};
localparam logic [15:0] BCR_ASYNC = 16'h9D1F;         // power-on value
localparam logic [15:0] RCR_ALL   = 16'h0010;         // power-on value: full array refresh

localparam logic [2:0] ST_INIT = 3'd0, ST_CFG = 3'd1, ST_IDLE = 3'd2,
                       ST_LAT = 3'd3, ST_DATA = 3'd4, ST_PARKED = 3'd5, ST_ASYNC = 3'd6,
                       ST_SCFG = 3'd7;

// ---- pin registers (shared by both chips)
reg  [5:0]  p_a = 6'd0;
reg  [15:0] p_dq0 = 16'd0, p_dq1 = 16'd0;
reg         p_oe = 1'b0, p_adv_n = 1'b1, p_cre = 1'b0, p_oe_n = 1'b1, p_we_n = 1'b1;
reg  [1:0]  p_ce_n = 2'b11;                           // {die 1, die 0}
reg  [3:0]  p_bm_n = 4'hF;                            // {cram1 ub, lb, cram0 ub, lb}
reg         p_clk = 1'b0;

assign cram0_a = p_a;             assign cram1_a = p_a;
assign cram0_dq_out = p_dq0;      assign cram1_dq_out = p_dq1;
assign cram0_dq_oe = p_oe;        assign cram1_dq_oe = p_oe;
assign cram0_clk = p_clk;         assign cram1_clk = p_clk;
assign cram0_adv_n = p_adv_n;     assign cram1_adv_n = p_adv_n;
assign cram0_cre = p_cre;         assign cram1_cre = p_cre;
assign cram0_ce0_n = p_ce_n[0];   assign cram1_ce0_n = p_ce_n[0];
assign cram0_ce1_n = p_ce_n[1];   assign cram1_ce1_n = p_ce_n[1];
assign cram0_oe_n = p_oe_n;       assign cram1_oe_n = p_oe_n;
assign cram0_we_n = p_we_n;       assign cram1_we_n = p_we_n;
assign {cram1_ub_n, cram1_lb_n, cram0_ub_n, cram0_lb_n} = p_bm_n;

// ---- read capture: beat 0 into lo_q, beat 1 into hi_q (straight from the pins).
// While no read runs, {hi_q, lo_q} hold the write data of the request buffer.
wire [31:0] dq_in = {cram1_dq_in, cram0_dq_in};
reg  [31:0] lo_q = 32'd0, hi_q = 32'd0;
reg  [31:0] dq_r = 32'd0;          // FULL_RATE: input register
reg         cap = 1'b0, cap_hi = 1'b0;   // FULL_RATE: dq_r holds the word marked by smp
assign ddr_dout = {hi_q, lo_q};

// ---- control
reg         ph = 1'b0;            // 1: the coming edge is an F edge
reg  [2:0]  st = ST_INIT;
reg  [12:0] tmr = 13'd0;          // power-up wait / slot within a segment or config write
reg  [1:0]  cop = 2'd0;           // config write: {die, BCR(1)/RCR(0)}
reg         cpass = 1'b0;         // config pass: 0 asynchronous form (ST_CFG), 1 synchronous (ST_SCFG)
reg         parking = 1'b0, clk_run = 1'b0;
reg         rst_pend = 1'b1;
(* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *) reg park_m = 1'b0, park_s = 1'b0;   // park synchroniser (clk_74a reset_n)
reg         beat = 1'b0, last = 1'b0, cont = 1'b0, smp = 1'b0, smp_hi = 1'b0;
reg         aux_prio = 1'b0, aux_turn = 1'b0, aux_rd_done = 1'b0;

// the request on the pins (and the rest of a split read)
reg         act_busy = 1'b0, act_we = 1'b0;
// the segment on the pins: an aux access (inputs held until aux_done) and its direction
reg         seg_aux = 1'b0, cur_we = 1'b0;
reg  [21:0] act_adr = 22'd0;
reg  [7:0]  act_cnt = 8'd0;
reg  [63:0] act_din = 64'd0;
reg  [7:0]  act_be = 8'd0;
// one accepted request waiting behind it (data in {hi_q, lo_q})
reg         b_v = 1'b0, b_we = 1'b0;
reg  [21:0] b_adr = 22'd0;
reg  [7:0]  b_cnt = 8'd0;
reg  [7:0]  b_be = 8'd0;

// ddr_busy from registers only: low whenever the buffer is free and no read
// owns the capture registers, in any clk cycle
wire rd_on = (act_busy && !act_we) || (!cur_we && (st == ST_LAT || st == ST_DATA)) || (FULL_RATE && (smp || cap));
// FULL_RATE: these states use every clk edge, the others every other one
wire fast_st = st == ST_IDLE || st == ST_LAT || st == ST_DATA || st == ST_SCFG;
wire f_edge = FULL_RATE ? (ph || fast_st) : ph;
assign ddr_busy = b_v || rd_on || rst_pend || park_s || aux_turn ||
                  !(st == ST_IDLE || st == ST_LAT || st == ST_DATA);

wire bus_req  = ddr_rd || ddr_we;
wire take_bus = !ddr_busy && bus_req;
wire row_end  = &act_adr[ROW_LOG2-1:0];

// a waiting aux access goes before the rest of a DDR request (a read paused
// between two words, see ST_DATA) when it is aux's turn; turns alternate
wire        aux_want = aux_req && !aux_rd_done;
wire        aux_go  = aux_want && aux_prio && !rst_pend;
// launch source (slot 0): aux by turn, the rest of a read, the buffer, the DDR port, aux
wire        sel_act = !aux_go && act_busy;
wire        sel_buf = !aux_go && !act_busy && b_v;
wire        sel_bus = !aux_go && !act_busy && !b_v && take_bus;
wire        sel_aux = !sel_act && !sel_buf && !sel_bus;
wire [21:0] aux_adr = SAVE_AUX != 0 && aux_addr[20] ? {3'b100, aux_addr[19:1]} : {8'h08, aux_addr[14:1]};
wire [21:0] l_adr = sel_act ? act_adr : sel_buf ? b_adr : sel_bus ? ddr_addr[21:0] : aux_adr;
wire        l_we  = sel_act ? act_we : sel_buf ? b_we : sel_bus ? ddr_we : aux_we;
wire        l_beat = sel_aux && aux_addr[0];   // an aux access is one beat

wire [15:0] cfg_val = parking ? BCR_ASYNC : cop[0] ? BCR_SYNC : RCR_ALL;

task automatic pins_idle;
    begin
        p_ce_n <= 2'b11; p_adv_n <= 1'b1; p_we_n <= 1'b1; p_oe_n <= 1'b1;
        p_oe <= 1'b0; p_bm_n <= 4'hF;
    end
endtask

task automatic launch;
    begin
        if (sel_buf) begin
            act_we <= b_we; act_adr <= b_adr; act_cnt <= b_cnt;
            act_din <= {hi_q, lo_q}; act_be <= b_be; b_v <= 1'b0;
        end else if (sel_bus) begin
            act_we <= ddr_we; act_adr <= ddr_addr[21:0];
            act_cnt <= ddr_we ? 8'd1 : ddr_burstcnt; act_din <= ddr_din; act_be <= ddr_be;
        end
        if (!sel_aux) act_busy <= 1'b1;            // aux leaves the DDR request untouched
        seg_aux <= sel_aux; cur_we <= l_we; aux_prio <= !sel_aux;
        st <= ST_LAT; tmr <= 13'd1; beat <= 1'b0; last <= 1'b0;
        p_ce_n <= l_adr[21] ? 2'b01 : 2'b10;
        p_adv_n <= 1'b0; p_we_n <= !l_we; p_oe_n <= 1'b1; p_oe <= 1'b1;
        p_a <= l_adr[20:15]; p_dq0 <= {l_adr[14:0], l_beat}; p_dq1 <= {l_adr[14:0], l_beat};
        p_bm_n <= l_we ? 4'hF : 4'h0;
    end
endtask

// after a configuration-register write (F edge): the next register, the
// other pass, or done. Start: asynchronous pass, clock on, synchronous pass,
// idle. Park (BCRs only): synchronous pass, clock off, asynchronous pass.
task automatic cfg_next;
    begin
        tmr <= 13'd0;
        cop <= cop + (parking ? 2'd2 : 2'd1);
        if (rst_pend) begin st <= ST_INIT; clk_run <= 1'b0; end
        else if (cop == 2'd3) begin
            cop <= parking ? 2'd1 : 2'd0;
            cpass <= !cpass;
            if (cpass == parking) begin
                // first pass done: the other form
                st <= parking ? ST_CFG : ST_SCFG;
                clk_run <= !parking;
            end else st <= parking ? ST_PARKED : ST_IDLE;
        end
    end
endtask

always @(posedge clk) begin
    ph <= !ph;
    rst_pend <= rst_pend || reset;
    park_m <= park === 1'b1;
    park_s <= park_m;
    ddr_dout_ready <= 1'b0;
    aux_done <= 1'b0;
    card_write <= 1'b0;
    aux_turn <= aux_req && !aux_rd_done && aux_prio;   // stop filling the buffer: aux goes next

    // a request taken in any cycle that does not go to the pins at once
    if (take_bus && !(f_edge && st == ST_IDLE && !aux_go)) begin
        b_v <= 1'b1; b_we <= ddr_we; b_adr <= ddr_addr[21:0];
        b_cnt <= ddr_we ? 8'd1 : ddr_burstcnt; b_be <= ddr_be;
        lo_q <= ddr_din[31:0]; hi_q <= ddr_din[63:32];
    end

    if (FULL_RATE) smp <= 1'b0;
    if (!FULL_RATE && !ph) begin
        // ---- R edge: the chip clock rises; read capture
        p_clk <= clk_run;
        smp <= 1'b0;
        if (smp) begin
            if (seg_aux) begin
                if (!rst_pend && !reset) begin aux_dout <= dq_in; aux_done <= 1'b1; end
                aux_rd_done <= 1'b1;       // aux_req may still be high at the next F edge
            end else if (!smp_hi) lo_q <= dq_in;
            else begin
                hi_q <= dq_in;
                if (!rst_pend && !reset) ddr_dout_ready <= 1'b1;
            end
        end
    end
    if (f_edge) begin
        // ---- F edge: the chip clock falls; every other pin changes here
        p_clk <= 1'b0;
        aux_rd_done <= 1'b0;

        case (st)
        ST_INIT: begin
            clk_run <= 1'b0; act_busy <= 1'b0; p_cre <= 1'b0;
            pins_idle();
            tmr <= reset ? 13'd0 : tmr + 13'd1;
            if (!reset && tmr == 13'(INIT_SLOTS)) begin
                st <= ST_CFG; tmr <= 13'd0; cop <= 2'd0; cpass <= 1'b0; parking <= 1'b0; rst_pend <= 1'b0;
            end
        end

        // asynchronous configuration-register write, clock stopped: slot 0 CRE
        // high, 1 CE#/ADV# low with the value on A and DQ, 2-3 WE# low,
        // 5 CE# high, 6 CRE low
        ST_CFG: begin
            tmr <= tmr + 13'd1;
            case (tmr[2:0])
            3'd0: p_cre <= 1'b1;
            3'd1: begin
                p_ce_n <= cop[1] ? 2'b01 : 2'b10; p_adv_n <= 1'b0;
                p_a <= (parking || cop[0]) ? 6'b001000 : 6'b000000;  // A[19:18]: 10 BCR, 00 RCR
                p_dq0 <= cfg_val; p_dq1 <= cfg_val; p_oe <= 1'b1;
            end
            3'd2: begin p_adv_n <= 1'b1; p_we_n <= 1'b0; end
            3'd4: p_we_n <= 1'b1;
            3'd5: p_ce_n <= 2'b11;
            3'd6: begin p_cre <= 1'b0; p_oe <= 1'b0; cfg_next(); end
            default: ;
            endcase
        end

        // synchronous configuration-register write, clock running: slot 0 CRE
        // high, 1 CE#/ADV#/WE# low with the value on A and DQ (sampled at the
        // next chip edge), 2 ADV#/WE# high, 10 CE# high (nine chip cycles
        // low, past any latency code), 11 CRE low
        ST_SCFG: begin
            tmr <= tmr + 13'd1;
            case (tmr[3:0])
            4'd0: p_cre <= 1'b1;
            4'd1: begin
                p_ce_n <= cop[1] ? 2'b01 : 2'b10; p_adv_n <= 1'b0; p_we_n <= 1'b0;
                p_a <= (parking || cop[0]) ? 6'b001000 : 6'b000000;
                p_dq0 <= cfg_val; p_dq1 <= cfg_val; p_oe <= 1'b1;
            end
            4'd2: begin p_adv_n <= 1'b1; p_we_n <= 1'b1; end
            4'd10: p_ce_n <= 2'b11;
            4'd11: begin p_cre <= 1'b0; p_oe <= 1'b0; cfg_next(); end
            default: ;
            endcase
        end

        ST_PARKED: begin
            pins_idle();
            if (rst_pend) begin st <= ST_INIT; tmr <= 13'd0; end
            else if (!park_s) begin st <= ST_CFG; tmr <= 13'd0; cop <= 2'd0; cpass <= 1'b0; parking <= 1'b0; end
            else if (aux_req) begin
                // asynchronous single access (power-on mode, clock stopped): halfword
                // 0x40000 + aux_addr on die 0, cram0 = aux bits 15:0, cram1 = 31:16
                st <= ST_ASYNC; tmr <= 13'd1;
                p_ce_n <= 2'b10; p_adv_n <= 1'b0; p_oe <= 1'b1; p_bm_n <= 4'h0;
                p_a <= 6'd4; p_dq0 <= {1'b0, aux_addr[14:0]}; p_dq1 <= {1'b0, aux_addr[14:0]};
            end
        end

        // slot 0 CE#/ADV# low with the address, 1 ADV# high (write: WE# low),
        // 2 write data / read bus released, 3 OE# low, write: 4 WE# high,
        // 5 CE# high and done (a read takes its data then, 147 ns after ADV#)
        ST_ASYNC: begin
            tmr <= tmr + 13'd1;
            case (tmr[2:0])
            3'd1: begin p_adv_n <= 1'b1; p_we_n <= !aux_we; end
            3'd2: begin
                if (aux_we) begin p_dq0 <= aux_din[15:0]; p_dq1 <= aux_din[31:16]; end
                else p_oe <= 1'b0;
            end
            3'd3: p_oe_n <= aux_we;
            3'd4: p_we_n <= 1'b1;
            3'd5: begin
                // Full rate: from the input register, so no pin path reaches
                // aux_dout (dq_r is the pin one clk earlier, 44 ns after OE#).
                if (!aux_we) aux_dout <= FULL_RATE ? dq_r : dq_in;
                pins_idle(); aux_done <= !rst_pend; st <= ST_PARKED;
            end
            default: ;
            endcase
        end

        ST_IDLE: begin
            if (act_busy && rst_pend && !act_we) act_busy <= 1'b0;   // drop reads under reset
            else if (!act_busy && b_v && rst_pend && !b_we) b_v <= 1'b0;
            else if (FULL_RATE && (smp || cap)) ;                   // a read's last words are still in the input pipeline
            // one launch site (sel_* picks the source); a waiting aux access
            // also goes before parking
            else if (aux_want && !rst_pend || act_busy || b_v || take_bus) launch();
            else if (rst_pend) begin st <= ST_INIT; tmr <= 13'd0; clk_run <= 1'b0; end
            else if (park_s) begin
                st <= ST_SCFG; tmr <= 13'd0; cop <= 2'd1; cpass <= 1'b1; parking <= 1'b1;
            end
        end

        ST_LAT: begin
            p_adv_n <= 1'b1; p_we_n <= 1'b1;
            if (!cur_we) begin p_oe <= 1'b0; if (tmr >= 13'(OE_SLOT)) p_oe_n <= 1'b0; end
            tmr <= tmr + 13'd1;
            if (tmr == 13'((cur_we ? WR_FIRST : RD_FIRST) - 1)) st <= ST_DATA;
        end

        ST_DATA: begin
            if (!cur_we) begin
                if (last) begin pins_idle(); st <= ST_IDLE; end
                else begin
                    smp <= 1'b1; smp_hi <= beat; beat <= !beat;
                    if (seg_aux) last <= 1'b1;
                    else if (beat) begin
                        act_adr <= act_adr + 22'd1; act_cnt <= act_cnt - 8'd1;
                        if (act_cnt == 8'd1) act_busy <= 1'b0;
                        // pause after this word for a waiting aux access (resumed from act_adr)
                        if (act_cnt == 8'd1 || row_end || rst_pend || aux_want && aux_prio) last <= 1'b1;
                    end
                end
            end else if (seg_aux) begin
                // aux write: one beat, all four bytes
                if (last) begin pins_idle(); st <= ST_IDLE; end
                else begin
                    p_dq0 <= aux_din[15:0]; p_dq1 <= aux_din[31:16]; p_bm_n <= 4'h0; last <= 1'b1;
                    if (!rst_pend) aux_done <= 1'b1;
                end
            end else if (beat) begin
                p_dq0 <= act_din[47:32]; p_dq1 <= act_din[63:48]; p_bm_n <= ~act_be[7:4];
                act_adr <= act_adr + 22'd1; act_busy <= 1'b0; beat <= 1'b0;
                card_write <= act_adr[21:14] == 8'h08;
                cont <= !row_end && !rst_pend && !park_s;
            end else if (act_busy) begin
                p_dq0 <= act_din[15:0]; p_dq1 <= act_din[31:16]; p_bm_n <= ~act_be[3:0];
                beat <= 1'b1;
            end else if (cont && b_v && b_we && b_adr == act_adr && !(aux_want && aux_prio)) begin
                // the buffered write is the next word: continue the burst
                act_din <= {hi_q, lo_q}; act_be <= b_be; act_busy <= 1'b1; b_v <= 1'b0; aux_prio <= 1'b1;
                p_dq0 <= lo_q[15:0]; p_dq1 <= lo_q[31:16]; p_bm_n <= ~b_be[3:0];
                beat <= 1'b1;
            end else begin
                pins_idle(); st <= ST_IDLE;
            end
        end

        default: st <= ST_INIT;
        endcase
    end

    if (FULL_RATE) begin
        // input register, then the word smp marked one clk earlier
        dq_r <= dq_in;
        cap <= smp; cap_hi <= smp_hi;
        p_clk <= clk_run;
        if (cap) begin
            if (seg_aux) begin
                if (!rst_pend && !reset) begin aux_dout <= dq_r; aux_done <= 1'b1; end
                aux_rd_done <= 1'b1;
            end else if (!cap_hi) lo_q <= dq_r;
            else begin
                hi_q <= dq_r;
                if (!rst_pend && !reset) ddr_dout_ready <= 1'b1;
            end
        end
    end
end

endmodule

`default_nettype wire
