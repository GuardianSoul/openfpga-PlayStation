// SPDX-License-Identifier: GPL-2.0-only
// Glue between the disc-swap sequencer and the CD stream loader.
// The FFVII build has one deferred, runtime-reloadable disc data slot (slot
// 0, a .bin). When the user loads the next disc's file, the loader reports
// disc_pending; psx_disc_swap then opens the lid, asks for the change and
// closes the lid after the loader's disc_change_done. The loader drains old
// transfers and rejects stale sectors before signalling done, so the lid
// only closes on the new disc. A swap strobes the loader once with slot 0.
module psx_disc_select #(
    parameter logic [15:0] DISC_SLOT = 16'h0000
) (
    input  wire        clk,
    input  wire        reset,
    input  wire        change_req,         // from psx_disc_swap
    output wire        change_done,        // to psx_disc_swap
    output wire [15:0] disc_slot_id,
    output reg         disc_select_strobe = 0,
    input  wire        disc_change_done    // from the loader, after drain
);
    reg change_req_d = 0;
    assign disc_slot_id = DISC_SLOT;
    always @(posedge clk) begin
        change_req_d <= reset ? 1'b0 : change_req;
        disc_select_strobe <= !reset && change_req && !change_req_d;
    end
    assign change_done = disc_change_done;
endmodule
