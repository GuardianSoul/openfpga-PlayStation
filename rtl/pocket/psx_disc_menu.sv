// SPDX-License-Identifier: MIT
// Multi-disc games as one Pocket game file (prepare --disc-menu): every disc
// of a game is listed in the same instance JSON (disc n's track k in slot
// FIRST_SLOT + (n-1)*STEP + k-1), so the game keeps one memory card, and the
// core menu's "Disc" setting picks the disc. Replaces psx_disc_select next to
// psx_disc_swap:
//  - while the wanted disc differs from the one last selected, swap_request
//    stays high, so psx_disc_swap opens the lid, asks for the change and
//    closes the lid once the loader has remounted;
//  - on each change request (also one raised by an APF 0x8A reload),
//    disc_select_strobe pulses once with the wanted disc's first slot.
// The setting is a clk_74a level; it is synchronized bit by bit and only
// taken when two consecutive samples agree. Reset starts at disc 1, which
// the loader mounts first (its DEFAULT_SLOT). The Disc list is per core and
// remembered, so it can name a disc the game file does not have (Disc 4 for
// FFVII, or the disc another game was left on): only discs the loader found
// in the APF table (discs_present) are taken; any other choice keeps the
// current disc.
module psx_disc_menu #(
    parameter [15:0] FIRST_SLOT=16'd101,
    parameter [15:0] STEP=16'd100,
    parameter integer DISCS=4
) (
    input wire clk, reset,
    input wire [1:0] disc_setting,          // clk_74a level: disc number - 1
    input wire change_req,                  // from psx_disc_swap
    output wire change_done,                // to psx_disc_swap
    output reg [15:0] disc_slot_id=FIRST_SLOT,
    output reg disc_select_strobe=0,
    input wire disc_change_done,            // from the loader
    input wire [3:0] discs_present,         // from the loader (after its first mount)
    output wire swap_request                // to psx_disc_swap
);
    (* async_reg="true", preserve, dont_merge *) reg [1:0] disc_meta=0;
    reg [1:0] disc_sync=0, disc_last=0, wanted=0, selected=0;
    reg change_req_d=0;
    wire takeable=disc_last<=DISCS-1 && discs_present[disc_last];
    assign swap_request=wanted!=selected;
    assign change_done=disc_change_done;
    always @(posedge clk) begin
        if(reset) begin
            disc_meta<=0;disc_sync<=0;disc_last<=0;wanted<=0;selected<=0;change_req_d<=0;
            disc_slot_id<=FIRST_SLOT;disc_select_strobe<=0;
        end else begin
            disc_meta<=disc_setting;disc_sync<=disc_meta;disc_last<=disc_sync;
            if(disc_sync==disc_last && takeable) wanted<=disc_last;
            change_req_d<=change_req;
            disc_select_strobe<=change_req && !change_req_d;
            if(change_req && !change_req_d) begin
                case(wanted)   // constants, no multiplier
                    2'd0: disc_slot_id<=FIRST_SLOT;
                    2'd1: disc_slot_id<=FIRST_SLOT+STEP;
                    2'd2: disc_slot_id<=FIRST_SLOT+STEP+STEP;
                    default: disc_slot_id<=FIRST_SLOT+STEP+STEP+STEP;
                endcase
                selected<=wanted;
            end
        end
    end
endmodule
