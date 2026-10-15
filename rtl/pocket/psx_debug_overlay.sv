// SPDX-License-Identifier: MIT
// Hardware bring-up readout drawn over the PSX picture: eight 32-bit words,
// MSB left, one CELL_W-pixel cell per bit (white 1, dark blue 0) in the
// top-left of the active area, CELL_H lines per row. Positions count active pixels
// (DE with CE) from each line's DE start and active lines from VS, so the
// readout follows the picture whatever the scaler does with blanking.
`default_nettype none
module psx_debug_overlay #(parameter integer CELL_W=6, CELL_H=5) (
    input wire clk, ce, de, vs,
    input wire [23:0] rgb_in,
    input wire [255:0] words,
    output wire [23:0] rgb_out
);
    // Display-only samples of counters from other clock domains.
    (* async_reg="true" *) reg [255:0] words_meta=0;
    reg [255:0] words_sync=0;
    always @(posedge clk) begin words_meta<=words; words_sync<=words_meta; end
    reg [9:0] px=0, line=0;
    reg de_d=0, vs_d=0;
    always @(posedge clk) begin
        de_d<=de; vs_d<=vs;
        if (vs && !vs_d) line<=0;
        else if (!de && de_d && line!=10'h3ff) line<=line+1'b1;
        if (!de) px<=0;
        else if (ce && px!=10'h3ff) px<=px+1'b1;
    end
    // Column = px/6 and row = line/5 by small counters, not dividers.
    reg [3:0] sub=0; reg [5:0] column=0;
    always @(posedge clk) begin
        if (!de) begin sub<=0; column<=0; end
        else if (ce) begin
            if (sub==CELL_W-1) begin sub<=0; column<=column+1'b1; end
            else sub<=sub+1'b1;
        end
    end
    reg [3:0] lsub=0; reg [3:0] row=0;
    always @(posedge clk) begin
        if (vs && !vs_d) begin lsub<=0; row<=0; end
        else if (!de && de_d) begin
            if (lsub==CELL_H-1) begin lsub<=0; if (row!=4'hf) row<=row+1'b1; end
            else lsub<=lsub+1'b1;
        end
    end
    wire in_area=de && row<4'd8 && column<6'd32;
    wire [31:0] word=words_sync[row[2:0]*32 +: 32];
    wire bit_value=word[5'd31-column[4:0]];
    wire in_cell=sub!=CELL_W-1 && lsub!=CELL_H-1;
    assign rgb_out=!in_area ? rgb_in : !in_cell ? 24'h000000 : bit_value ? 24'hffffff : 24'h1830a0;
endmodule
`default_nettype wire
