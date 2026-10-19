// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Memory card 1 save slot: moves the APF nonvolatile data slot "Memory Card 1"
// (bridge window BASE, 131,072 bytes) between the APF bridge (clk_74a) and
// the memory card 1 window of the VRAM controller's aux port (clk2x), and
// formats a card that has no "MC" magic once the load is complete.
// Written from docs/clean-room-memory.md (Block 2), the APF bridge peripheral
// (MIRLO64 platform/pocket/io_bridge_peripheral.v) and psx-spx.
//
// APF side (clk_74a)
// ------------------
// * Window: bridge_addr[31:17] == BASE[31:17]; word index = bridge_addr[16:2].
//   Accesses outside the window are ignored. bridge_wr / bridge_rd are used
//   as strobes: the APF bridge peripheral drives each for exactly one clock.
// * Byte order: card byte 4*i+k is bits 8k+7..8k of PSRAM word i (aux_din /
//   aux_dout, little-endian, MiSTer's card layout). BRIDGE_LITTLE must equal
//   the top's bridge_endian_little: 1 (release top, as psx_bios_loader.sv
//   assumes) means the bridge already delivers file byte 0 in bits [7:0] and
//   no swap is done here; 0 means words arrive big-endian (file byte 0 in
//   [31:24]) and both directions are byte-swapped here.
// * One request register (req_*_74) holds one APF access. It is handed to
//   clk2x by a toggle, drives the aux port directly while the access runs,
//   and is freed by the take toggle that clk2x returns on aux_done. A strobe
//   that arrives while it is still busy is dropped and sets the sticky
//   `overflow` (data lost: a write, or a read answer that came too late).
// * Reads (APF save readback): the bridge buffers reads by one word: each
//   read transaction samples bridge_rd_data (the answer to the PREVIOUS read)
//   four clocks after the address is complete and then pulses bridge_rd for
//   the new address. When the take toggle of a read returns, aux_dout (held
//   by the VRAM controller until its next aux read, which only this block
//   starts) is loaded into bridge_rd_data, a plain clk_74a register driving
//   the top's mux. Analogue states the fastest bridge transaction rate as one
//   per 88 clk_74a cycles (io_bridge_peripheral.v header). Strobe to
//   bridge_rd_data takes about ten clk_74a cycles plus the aux latency; the
//   bench shows every aux access up to 70 clk2x is safe at that rate (the
//   real controller answers in 11-12 clk2x with no GPU traffic).
//
// clk2x side
// ----------
// * One aux access at a time. aux_req is a register held from the request
//   until the one-clock aux_done. aux_we/aux_addr/aux_din are selected from
//   stable registers: the APF request register (clk_74a, bundled data, see
//   below) or the format counter. APF requests have priority over the
//   format engine. With no APF traffic and the check done, aux_req stays low:
//   the block takes no aux bandwidth from the guest.
// * Check/format: load_done counts once clk2x has taken every APF request
//   issued before it (ready_tgl_74). Word 0's magic is known without an aux
//   read when APF wrote word 0 during the load (w0_seen / w0_magic, taken
//   from the write data); otherwise (no file) the card is formatted: PSRAM
//   keeps the previous game's card across core loads, and a game without a
//   card file would inherit it. If card bytes 0-1 are not 'M','C' (16'h434D little-endian) the header area
//   (frames 0-63, words 0..2047) is written as a factory-formatted card
//   (psx-spx; same image as psx_memcard_blank.sv). Word 0 (the magic) is
//   written LAST, so a reset during the format leaves the card unformatted
//   and the next check formats it again. A card with the magic is never
//   written; after the check nothing is formatted again until reset.
// * Format writes go back to back: the next one is presented in the clock
//   after aux_done with aux_req still high (the controller treats aux_req in
//   the clock after aux_done as a new request, as the "held until aux_done"
//   contract implies; psx_vram_psram does). Other requests leave aux_req low
//   for at least one clock.
// * busy is a clk2x register: high from reset until the decision is made
//   and any format is finished. The decision waits for the last APF write's
//   aux_done, so busy falls only after every APF-written word has completed
//   in PSRAM; it falls on the edge that sees that aux_done (or the last
//   format write's).
// * card_write is not needed and is ignored.
//
// Save-state window (SAVE_BASE != 0)
// ----------------------------------
// A second bridge window, bridge_addr[31:22] == SAVE_BASE[31:22] (4 MiB),
// shares the request register and the aux port: its word index goes out as
// aux_addr = {1'b1, bridge_addr[21:2]}, which psx_vram_psram (SAVE_AUX)
// maps to the save buffer at the start of PSRAM die 1. The card check and
// format only look at card words (aux_addr[20] low).
//
// Reset
// -----
// reset_2x (active high, clk2x) starts a four-phase handshake: rst_req_2x is
// set, the clk_74a side sees it (s74_rst[1]) and holds its side in reset
// (request toggle cleared, APF strobes ignored), clk2x waits until that
// acknowledgement has risen and fallen again after reset_2x is released. So
// both sides leave reset with all toggles at zero, for any reset_2x pulse
// width. An aux access in flight is abandoned (the VRAM controller shares
// reset_2x). APF writes during reset are lost; the check runs again after.
//
// Clock-domain crossing registers (constraint list)
// -------------------------------------------------
// clk_74a -> clk2x, two-flop synchronisers (first stage in [0]):
//   req_tgl_74   -> s2x_req[0], s2x_req[1]     request toggle
//   ready_74     -> s2x_ready[0], s2x_ready[1] load_done
//   s74_rst[1]   -> s2x_rack[0], s2x_rack[1]   reset acknowledge
// clk_74a -> clk2x, bundled data (launched on the same clk_74a edge as the
// toggle, stable until the take toggle returns; used only after s2x_req[1]
// differs from req_seen_2x, i.e. at least two clk2x edges later):
//   req_we_74, req_addr_74[20:0], req_data_74[31:0]
//     -> w0_seen, w0_magic, and through aux_we/aux_addr/aux_din into the
//        VRAM controller's clk2x registers (psx_vram_psram act_*, p_a, p_dq*,
//        p_we_n, p_bm_n, st, tmr, aux_dout ...)
//   ready_tgl_74 (request toggle value covered by load_done; frozen on the
//   edge ready_74 rises) -> ready_seen (used only while s2x_ready[1] is high)
// clk2x -> clk_74a, two-flop synchronisers:
//   rst_req_2x   -> s74_rst[0], s74_rst[1]     reset request
//   take_tgl_2x  -> s74_take[0], s74_take[1]   access finished
// clk2x -> clk_74a, bundled data (the VRAM controller's aux_dout register,
// loaded on the edge that raises aux_done, one clock before take_tgl_2x
// toggles, unchanged until the next aux read, which needs a new request):
//   psx_vram_psram aux_dout[31:0] -> bridge_rd_data[31:0]
// Every other register stays in one domain. `overflow` is a clk_74a register.
`default_nettype none
module psx_memcard_slot #(
    parameter [31:0] BASE = 32'h2000_0000,
    parameter        BRIDGE_LITTLE = 1,
    parameter [31:0] SAVE_BASE = 32'h0,   // save-state window (0 = none)
    // Test-only fault injection for the bench's negative controls (0 = none).
    parameter integer FAULT = 0
) (
    input  wire        clk_74a,
    input  wire [31:0] bridge_addr,
    input  wire        bridge_wr,
    input  wire [31:0] bridge_wr_data,
    input  wire        bridge_rd,
    output reg  [31:0] bridge_rd_data = 32'd0,
    output reg         overflow = 1'b0,

    input  wire        clk2x,
    input  wire        reset_2x,
    output reg         busy = 1'b1,
    output reg         aux_req = 1'b0,
    output wire        aux_we,
    output wire [20:0] aux_addr,
    output wire [31:0] aux_din,
    input  wire [31:0] aux_dout,
    input  wire        aux_done,
    input  wire        card_write,
    input  wire        load_done
);
    function automatic [31:0] swap32(input [31:0] w);
        swap32 = {w[7:0], w[15:8], w[23:16], w[31:24]};
    endfunction

    // Factory-formatted header area, 32-bit word w of frames 0-63 (psx-spx).
    function automatic [31:0] blank_word(input [10:0] w);
        reg [5:0] frame; reg [4:0] slot;
        begin
            frame = w[10:5]; slot = w[4:0]; blank_word = 32'd0;
            if (frame == 6'd0 || frame == 6'd63) begin
                if (slot == 5'd0)  blank_word = 32'h0000_434D;   // 'M','C'
                if (slot == 5'd31) blank_word = 32'h0E00_0000;   // XOR checksum
            end else if (frame < 6'd16) begin                    // free directory entry
                if (slot == 5'd0)  blank_word = 32'h0000_00A0;
                if (slot == 5'd2)  blank_word = 32'h0000_FFFF;   // no next block
                if (slot == 5'd31) blank_word = 32'hA000_0000;
            end else if (frame < 6'd36) begin                    // no broken sector
                if (slot == 5'd0)  blank_word = 32'hFFFF_FFFF;
                if (slot == 5'd2)  blank_word = 32'h0000_FFFF;
            end
        end
    endfunction

    // ------------------------------------------------------------------
    // clk_74a side
    // ------------------------------------------------------------------
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s74_rst = 2'b11;
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s74_take = 2'b00;
    reg        req_tgl_74 = 1'b0, ready_tgl_74 = 1'b0, ready_74 = 1'b0, take_seen_74 = 1'b0;
    reg        req_we_74 = 1'b0;
    reg [20:0] req_addr_74 = 21'd0;   // [20] save window, else card word in [14:0]
    reg [31:0] req_data_74 = 32'd0;
    wire       rst_74 = s74_rst[1];
    wire       in_save = SAVE_BASE != 0 && bridge_addr[31:22] == SAVE_BASE[31:22];
    wire       in_window = bridge_addr[31:17] == BASE[31:17] || in_save;
    wire       wr_strobe = bridge_wr && in_window;
    wire       rd_strobe = bridge_rd && in_window;
    wire       pending = req_tgl_74 != s74_take[1];
    wire       req_accept = (wr_strobe || rd_strobe) && !pending;
    wire       take_edge = s74_take[1] != take_seen_74;
    wire       rdv_edge = take_edge && !req_we_74;     // a read's answer is in aux_dout
    wire [31:0] wr_word = (BRIDGE_LITTLE != 0) ^ (FAULT == 1) ? bridge_wr_data : swap32(bridge_wr_data);

    always @(posedge clk_74a) begin
        s74_take <= {s74_take[0], take_tgl_2x};
        s74_rst  <= {s74_rst[0],  rst_req_2x};
        take_seen_74 <= s74_take[1];
        if (rdv_edge) bridge_rd_data <= (BRIDGE_LITTLE != 0) ? aux_dout : swap32(aux_dout);
        if (rst_74) begin
            req_tgl_74 <= 1'b0; ready_tgl_74 <= 1'b0; ready_74 <= 1'b0; overflow <= 1'b0;
        end else begin
            if ((wr_strobe || rd_strobe) && pending) overflow <= 1'b1;
            if (req_accept) begin
                req_we_74 <= wr_strobe;
                req_addr_74 <= in_save ? {1'b1, bridge_addr[21:2]} : {6'd0, bridge_addr[16:2]};
                req_data_74 <= wr_word; req_tgl_74 <= !req_tgl_74;
            end
            // ready_74 carries the request toggle count it covers: clk2x
            // accepts it only once it has taken every request up to then.
            ready_74 <= load_done;
            if (!ready_74) ready_tgl_74 <= req_tgl_74 ^ req_accept;
        end
    end

    // ------------------------------------------------------------------
    // clk2x side
    // ------------------------------------------------------------------
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s2x_req = 2'b00;
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s2x_ready = 2'b00;
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s2x_rack = 2'b00;
    reg        rst_req_2x = 1'b1, req_seen_2x = 1'b0, take_tgl_2x = 1'b0;
    reg        own = 1'b0;            // aux access is the check read or a format write
    reg        ready_seen = 1'b0, checked = 1'b0, fmt_active = 1'b0;
    reg        w0_seen = 1'b0, w0_magic = 1'b0;
    reg [10:0] fmt_idx = 11'd1;
    wire       rst_2x = reset_2x || rst_req_2x || s2x_rack[1];
    wire       req_new = s2x_req[1] != req_seen_2x;
    wire [10:0] fmt_last_idx = FAULT == 5 ? 11'd1023 : 11'd2047;
    // Format sequence: words 1..last, then word 0 (the magic) last.
    wire        fmt_final = FAULT == 4 ? fmt_idx == fmt_last_idx : fmt_idx == 11'd0;
    wire [10:0] fmt_first = FAULT == 4 ? 11'd0 : 11'd1;
    wire [10:0] fmt_next  = (fmt_idx == fmt_last_idx && FAULT != 4) ? 11'd0 : fmt_idx + 11'd1;
    wire        magic_ok  = w0_magic && FAULT != 3;
    wire        decide_now = ready_seen && !checked && !fmt_active && w0_seen;

    assign aux_we   = own ? fmt_active : req_we_74;
    assign aux_addr = own ? {10'd0, fmt_idx & {11{fmt_active}}} : req_addr_74;
    assign aux_din  = own ? blank_word(fmt_idx) : req_data_74;

    always @(posedge clk2x) begin
        s2x_req   <= {s2x_req[0],   req_tgl_74};
        s2x_ready <= {s2x_ready[0], ready_74};
        s2x_rack  <= {s2x_rack[0],  rst_74};
        // Reset handshake: hold rst_req_2x until clk_74a acknowledged it.
        if (reset_2x) rst_req_2x <= 1'b1;
        else if (rst_req_2x && s2x_rack[1]) rst_req_2x <= 1'b0;

        if (rst_2x) begin
            req_seen_2x <= 1'b0; take_tgl_2x <= 1'b0; aux_req <= 1'b0; own <= 1'b0;
            ready_seen <= 1'b0; checked <= 1'b0; fmt_active <= 1'b0; fmt_idx <= 11'd1;
            w0_seen <= 1'b0; busy <= 1'b1;
        end else begin
            // load_done counts only once every request issued before it is taken
            if (s2x_ready[1] && req_seen_2x == ready_tgl_74 && !req_new) ready_seen <= 1'b1;
            if (aux_req) begin
                if (aux_done) begin
                    aux_req <= 1'b0;
                    if (!own) begin
                        if (!(FAULT == 6 && !req_we_74)) take_tgl_2x <= req_seen_2x;   // free the request register
                        if (req_we_74 && decide_now && !req_new) begin
                            // the last load write just completed: decide at once
                            if (magic_ok) begin checked <= 1'b1; busy <= 1'b0; end
                            else begin
                                fmt_active <= 1'b1; fmt_idx <= fmt_first; aux_req <= 1'b1; own <= 1'b1;
                            end
                        end
                    end else if (!fmt_active) begin              // check read
                        if (aux_dout[15:0] == 16'h434D && FAULT != 3) begin checked <= 1'b1; busy <= 1'b0; end
                        else begin fmt_active <= 1'b1; fmt_idx <= fmt_first; aux_req <= !req_new; end
                    end else if (fmt_final) begin                  // format done
                        fmt_active <= 1'b0; checked <= 1'b1; busy <= 1'b0;
                    end else begin
                        fmt_idx <= fmt_next;
                        aux_req <= !req_new;                         // next format write back to back
                    end
                end
            end else if (req_new) begin
                req_seen_2x <= s2x_req[1]; aux_req <= 1'b1; own <= 1'b0;
                if (FAULT == 6 && !req_we_74) take_tgl_2x <= s2x_req[1];   // fault: answer before the read
                if (req_we_74 && req_addr_74 == 21'd0) begin          // word 0 written by APF
                    w0_seen <= 1'b1; w0_magic <= req_data_74[15:0] == 16'h434D;
                end
            end else if (fmt_active) begin
                aux_req <= 1'b1; own <= 1'b1;
            end else if (decide_now) begin
                if (magic_ok) begin checked <= 1'b1; busy <= 1'b0; end
                else begin fmt_active <= 1'b1; fmt_idx <= fmt_first; end
            end else if (ready_seen && !checked) begin
                // no card file (APF wrote no word 0): a fresh card. PSRAM keeps
                // the last game's card across core loads, so never keep it.
                if (FAULT == 7) begin aux_req <= 1'b1; own <= 1'b1; end   // fault: the old check read
                else begin fmt_active <= 1'b1; fmt_idx <= fmt_first; end
            end
            if (FAULT == 2) busy <= !ready_seen || (!checked && !fmt_active);
        end
    end

    wire unused = card_write | |bridge_addr[1:0];
endmodule
`default_nettype wire
