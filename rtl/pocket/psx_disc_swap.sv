// SPDX-License-Identifier: GPL-2.0-only
// Disc-swap sequencer for the Pocket menu. With quiet high (the guest has
// read nothing since the last mount) a swap remounts without opening the lid.
//
// Two ways to change discs, as in most emulators:
//  - manual: the menu's "disc lid" toggle drives the lid directly; the CD
//    service may switch discs whenever the lid is open;
//  - automatic: one "swap disc" request opens the lid, holds it open for
//    OPEN_CYCLES so the game sees it, asks the CD service to switch to the
//    selected disc (change_req until change_done), waits CLOSE_CYCLES and
//    closes the lid. While a swap runs, further requests are ignored.
// With the CD stream service (psx_cd_stream: one user-reloadable disc slot),
// disc_pending means the user picked a new file that is not mounted yet:
//  - with the menu lid open, change_req is raised at once and the lid stays
//    as the user set it;
//  - with the lid closed, disc_pending starts the automatic swap.
// The CD controller sees a disc change through LIDopen and the new track
// table the service sends; nothing else needs to know.
module psx_disc_swap #(
    parameter int OPEN_CYCLES  = 33_868_800,   // 1 s at clk1x
    parameter int CLOSE_CYCLES = 16_934_400    // 0.5 s
) (
    input  wire clk,
    input  wire reset,
    input  wire manual_lid_open,   // menu toggle
    input  wire swap_request,      // menu action, level or pulse
    input  wire disc_pending,      // service: a new disc file is waiting
    input  wire quiet,             // the guest has read nothing from this disc yet: swap without the lid
    output reg  change_req = 0,    // to the CD service: switch to the selected disc now
    input  wire change_done,       // service: new track table sent, sectors from the new disc
    output wire lid_open,          // to cd_top LIDopen
    output wire busy
);
    typedef enum logic [1:0] {IDLE, OPENED, CHANGING, CLOSING} state_t;
    state_t state = IDLE;
    // Wide enough for the longer delay only (26 bits at the defaults).
    localparam int CW = $clog2((OPEN_CYCLES > CLOSE_CYCLES ? OPEN_CYCLES : CLOSE_CYCLES) + 1);
    reg [CW-1:0] count = 0;
    reg request_seen = 0, silent = 0;
    assign lid_open = manual_lid_open || (state != IDLE && !silent);
    assign busy = state != IDLE;
    always @(posedge clk) begin
        if (reset) begin
            state <= IDLE; count <= 0; change_req <= 0; request_seen <= 0; silent <= 0;
        end else begin
            case (state)
                IDLE: if (manual_lid_open && disc_pending) begin
                    change_req <= 1;                      // lid already open: switch now
                    if (change_done) change_req <= 0;
                end else if ((swap_request || disc_pending) && !request_seen && quiet) begin
                    // Nothing has read the disc since it was mounted (e.g. the
                    // remembered disc right after start): no lid, no delays.
                    state <= CHANGING; change_req <= 1; request_seen <= 1; silent <= 1;
                end else if ((swap_request || disc_pending) && !request_seen) begin
                    state <= OPENED; count <= CW'(OPEN_CYCLES); request_seen <= 1; change_req <= 0; silent <= 0;
                end else begin
                    change_req <= 0;
                    if (!swap_request) request_seen <= 0;
                end
                OPENED: if (count != 0) count <= count - 1;
                        else begin state <= CHANGING; change_req <= 1; end
                CHANGING: if (change_done) begin
                        change_req <= 0; count <= CW'(CLOSE_CYCLES);
                        state <= silent ? IDLE : CLOSING; silent <= 0;
                    end
                CLOSING: if (count != 0) count <= count - 1;
                         else state <= IDLE;
            endcase
            if (state != IDLE && !swap_request && !disc_pending) request_seen <= 0;
        end
    end
endmodule
