// SPDX-License-Identifier: MIT
// Stable CPU-reset level into the free-running APF video clock. Initialization
// is blank; neither CPU CE nor guest reset gates or resets the synchronizer.
// A level must span a video edge. psx_boot_gate holds every reset through
// three CPU edges, longer than two video periods in the supported clocks.
// Output changes 2-3 video periods after an asynchronous transition; the APF
// RGB register consumes it on the following edge. Sync/scaler metadata bypass
// this level, so blanking cannot stop the scaler or change its slot/field words.
`default_nettype none
module psx_video_blank_cdc (
    input wire video_clk,
    input wire blank_async,
    (* preserve, dont_merge *) output reg blank_video=1'b1
);
    (* async_reg="true", preserve, dont_merge *) reg blank_meta=1'b1;
    (* async_reg="true", preserve, dont_merge *) reg blank_sync=1'b1;
    // Preserve the consumer stage independently of the APF pixel registers.
    // The first two stages have no other consumers.
    always @(posedge video_clk) begin
        blank_meta<=blank_async;
        blank_sync<=blank_meta;
        blank_video<=blank_sync;
    end
endmodule
`default_nettype wire
