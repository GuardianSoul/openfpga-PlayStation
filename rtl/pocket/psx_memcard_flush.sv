// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Mid-session memory card write-back (public issue #18: a crash lost an hour
// of saves, because APF reads the nonvolatile card slot back to SD only when
// the core quits normally).
//
// After the game writes the card (card_write, a clk2x pulse from the VRAM
// controller for each guest write into the card window), the card is dirty.
// Once no card write has come for 2^QUIET_BITS - 1 clk_74a (QUIET_BITS 28:
// 3.6 s, so a whole multi-frame save has finished) and the card load is done
// (enable) and no save state is running (hold), this block sends one APF
// target "data slot write": slot SLOT_ID, offset 0, length LENGTH, from
// bridge BRIDGE. APF then reads the card through psx_memcard_slot's window,
// as it does at quit, and writes it to the slot's file. A card write during
// the flush makes the card dirty again (flushed again after the next quiet
// time). An error result is kept in last_error and is not retried until the
// next card write.
//
// The core_bridge_cmd target port is shared with psx_cue_mount (reads,
// getfile, openfile; one command at a time, one-clock request pulses, done
// falls then rises). A flush starts only while the cue side has no command
// in flight and none starting this clock. While the flush owns the port, a
// cue command is captured with its parameters and sent as soon as the flush
// has completed; the cue side sees done low from its request until its own
// command's done has fallen, so it never takes the flush's completion as its
// own. With no flush, every t_* output equals its c_* input and c_done equals
// t_done in the same clock (pure pass-through).
//
// psx_apf_target_arb (save states) is not used: its clients have read/write
// only, and the cue firmware's getfile/openfile commands and pulse handshake
// would need an adapter larger than this mux.
//
// busy: the flush owns the port or a delayed cue command is pending; OR it
// into the save device's cue_busy so a save state waits for it.
`default_nettype none
module psx_memcard_flush #(
    parameter integer QUIET_BITS = 28,
    parameter [15:0]  SLOT_ID = 16'h0010,
    parameter [31:0]  BRIDGE = 32'h2000_0000,
    parameter [31:0]  LENGTH = 32'h0002_0000
) (
    input  wire        clk,            // clk_74a
    input  wire        clk2x,
    input  wire        card_write,     // clk2x pulse: a guest write into the card
    input  wire        enable,         // async level: card loaded, core running
    input  wire        hold,           // async level: save state in progress
    // cue reader side
    input  wire        c_read, c_getfile, c_openfile,
    input  wire [15:0] c_id,
    input  wire [31:0] c_offset, c_bridge, c_length,
    input  wire        c_busy,         // cue command in flight
    output wire        c_done,
    // core_bridge_cmd target side
    output wire        t_read, t_write, t_getfile, t_openfile,
    output wire [15:0] t_id,
    output wire [31:0] t_offset, t_bridge, t_length,
    input  wire        t_done,
    input  wire [2:0]  t_error,
    output wire        busy,
    output reg         dirty = 1'b0,
    output reg  [2:0]  last_error = 3'd0,
    output reg  [7:0]  flushes = 8'd0
);
    reg wtgl_2x = 1'b0;
    always @(posedge clk2x) if (card_write) wtgl_2x <= !wtgl_2x;
    (* async_reg="true", preserve, dont_merge *) reg [2:0] wsync = 3'b000;
    (* async_reg="true", preserve, dont_merge *) reg [1:0] en_s = 2'b00, hold_s = 2'b00;
    wire wevent = wsync[2] ^ wsync[1];

    localparam [2:0] S_IDLE = 0, S_ISSUE = 1, S_WAIT = 2, S_REPLAY = 3, S_MASK = 4;
    reg [2:0] state = S_IDLE;
    reg low_seen = 1'b0;
    reg [QUIET_BITS-1:0] quiet = {QUIET_BITS{1'b0}};
    reg rp_pending = 1'b0;
    reg [1:0] rp_kind = 2'd0;          // 0 read, 1 getfile, 2 openfile
    reg [15:0] rp_id = 16'd0;
    reg [31:0] rp_offset = 32'd0, rp_bridge = 32'd0, rp_length = 32'd0;

    wire idle = state == S_IDLE;
    wire flush_own = state == S_ISSUE || state == S_WAIT;
    wire replay = state == S_REPLAY || state == S_MASK;
    wire c_pulse = c_read || c_getfile || c_openfile;
    assign t_write = state == S_ISSUE;
    assign t_read = idle ? c_read : state == S_REPLAY && rp_kind == 2'd0;
    assign t_getfile = idle ? c_getfile : state == S_REPLAY && rp_kind == 2'd1;
    assign t_openfile = idle ? c_openfile : state == S_REPLAY && rp_kind == 2'd2;
    assign t_id = flush_own ? SLOT_ID : replay ? rp_id : c_id;
    assign t_offset = flush_own ? 32'd0 : replay ? rp_offset : c_offset;
    assign t_bridge = flush_own ? BRIDGE : replay ? rp_bridge : c_bridge;
    assign t_length = flush_own ? LENGTH : replay ? rp_length : c_length;
    assign c_done = idle ? t_done : 1'b0;
    assign busy = !idle;

    always @(posedge clk) begin
        wsync <= {wsync[1:0], wtgl_2x};
        en_s <= {en_s[0], enable};
        hold_s <= {hold_s[0], hold};
        if (wevent) quiet <= {QUIET_BITS{1'b0}};
        else if (!(&quiet)) quiet <= quiet + 1'b1;
        if (wevent) dirty <= 1'b1;
        else if (!en_s[1]) dirty <= 1'b0;
        // a cue command while the port is taken waits with its parameters
        if (!idle && c_pulse) begin
            rp_pending <= 1'b1;
            rp_kind <= c_getfile ? 2'd1 : c_openfile ? 2'd2 : 2'd0;
            rp_id <= c_id; rp_offset <= c_offset; rp_bridge <= c_bridge; rp_length <= c_length;
        end
        case (state)
            S_IDLE: if (dirty && !wevent && &quiet && en_s[1] && !hold_s[1] && !c_busy && !c_pulse) begin
                    dirty <= 1'b0; state <= S_ISSUE;
                end
            S_ISSUE: begin low_seen <= 1'b0; state <= S_WAIT; end
            S_WAIT: begin
                    if (!t_done) low_seen <= 1'b1;
                    else if (low_seen) begin
                        last_error <= t_error; flushes <= flushes + 8'd1;
                        state <= rp_pending || c_pulse ? S_REPLAY : S_IDLE;
                    end
                end
            S_REPLAY: begin rp_pending <= 1'b0; state <= S_MASK; end
            S_MASK: if (!t_done) state <= S_IDLE;
            default: state <= S_IDLE;
        endcase
    end
endmodule
`default_nettype wire
