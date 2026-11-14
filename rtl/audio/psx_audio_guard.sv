// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Audio bridge reset policy (public issue #21, "no sound").
//
// Before: the bridge was held in reset while the fractional audio PLL's
// `locked` was low, and reset again whenever it dropped. A unit whose lock
// flag never rises or chatters stays silent or restarts LRCK over and over.
// Analogue's template cores ignore `locked`.
//
// Here, in the source clock (clk1x, 33.8688 MHz):
//  - clock_good latches once `locked` has stayed high for one whole tick
//    (2^TICK_BITS clocks, 0.97 ms at 15), or after TIMEOUT_TICKS ticks
//    (9.7 ms) whatever `locked` does. It stays set until rst_n (cpu_locked)
//    falls. The PLL runs from power-on either way; the timeout only stops
//    waiting for a flag the template cores never read.
//  - Recovery: the bridge's fatal fault (source overflow or missed output
//    deadline) mutes it until reset. If `fatal` stays high for RECOVER_TICKS
//    whole ticks (output is muted meanwhile), reset_async is raised for one
//    tick, which clears the fault and refills the FIFO; then counting starts
//    again. recovered (sticky) and timed_out (sticky) are diagnostics.
//  - reset_async = restart || !clock_good || recovering, registered.
module psx_audio_guard #(
    parameter integer TICK_BITS = 15,
    parameter integer TIMEOUT_TICKS = 10,
    parameter integer RECOVER_TICKS = 2
) (
    input  wire clk,
    input  wire rst_n,        // async, clk's PLL lock (cpu_locked)
    input  wire pll_locked,   // async, audio PLL lock
    input  wire restart,      // clk, game start/restart (ram_refresh)
    input  wire fatal,        // async, bridge status[0] (audio clock)
    output reg  reset_async = 1'b1,
    output reg  clock_good = 1'b0,
    output reg  timed_out = 1'b0,
    output reg  recovered = 1'b0
);
    (* async_reg="true", preserve, dont_merge *) reg lock_meta = 1'b0, lock_sync = 1'b0;
    (* async_reg="true", preserve, dont_merge *) reg fatal_meta = 1'b0, fatal_sync = 1'b0;
    reg [TICK_BITS-1:0] prescale = {TICK_BITS{1'b0}};
    reg [3:0] ticks = 4'd0;
    reg lock_dropped = 1'b1, recovering = 1'b0;
    wire tick = &prescale;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            lock_meta <= 1'b0; lock_sync <= 1'b0; fatal_meta <= 1'b0; fatal_sync <= 1'b0;
            prescale <= {TICK_BITS{1'b0}}; ticks <= 4'd0; lock_dropped <= 1'b1;
            recovering <= 1'b0; clock_good <= 1'b0; timed_out <= 1'b0; recovered <= 1'b0;
            reset_async <= 1'b1;
        end else begin
            lock_meta <= pll_locked; lock_sync <= lock_meta;
            fatal_meta <= fatal; fatal_sync <= fatal_meta;
            prescale <= prescale + 1'b1;
            if (!lock_sync) lock_dropped <= 1'b1;
            if (tick) begin
                lock_dropped <= !lock_sync;
                recovering <= 1'b0;
                if (!clock_good) begin
                    ticks <= ticks + 4'd1;
                    if (!lock_dropped && lock_sync) clock_good <= 1'b1;
                    else if (ticks == TIMEOUT_TICKS - 1) begin clock_good <= 1'b1; timed_out <= 1'b1; end
                end else if (fatal_sync && !recovering) begin
                    if (ticks == RECOVER_TICKS - 1) begin
                        ticks <= 4'd0; recovering <= 1'b1; recovered <= 1'b1;
                    end else ticks <= ticks + 4'd1;
                end else ticks <= 4'd0;
            end
            reset_async <= restart || !clock_good || recovering;
        end
    end
endmodule
