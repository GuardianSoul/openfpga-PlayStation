// SPDX-License-Identifier: MIT
// Copyright (c) 2026 PSX Pocket contributors
//
// Simulation-only: the DDR-word windows the psx_vram_psram bench exercises
// and their dense storage index (the chip model and the golden memory use the
// same index; anything else is a bench error). word = {die, word[20:0]} as
// psx_vram_psram maps ddr_addr[21:0]. Initial contents come from vp_seed.
function automatic integer vp_index(input [21:0] w);
    if (w < 22'h020000) vp_index = int'(w);                                         // VRAM 1 MiB
    else if (w < 22'h024000) vp_index = 32'h20000 + int'(w - 22'h020000);           // card 1
    else if (w >= 22'h040000 && w < 22'h044000) vp_index = 32'h24000 + int'(w - 22'h040000); // card 2
    else if (w >= 22'h060000 && w < 22'h070000) vp_index = 32'h28000 + int'(w - 22'h060000); // SPU
    else if (w >= 22'h080000 && w < 22'h0A0000) vp_index = 32'h38000 + int'(w - 22'h080000); // mirror / frame 0
    else if (w >= 22'h0E0000 && w < 22'h100000) vp_index = 32'h58000 + int'(w - 22'h0E0000); // frame 3, die 0 top
    else if (w >= 22'h200000 && w < 22'h204000) vp_index = 32'h78000 + int'(w - 22'h200000); // die 1 bottom
    else if (w >= 22'h3FC000)                   vp_index = 32'h7C000 + int'(w - 22'h3FC000); // die 1 top
    else vp_index = -1;
endfunction
localparam integer VP_WORDS = 32'h80000;

function automatic [15:0] vp_seed(input integer chip, input [21:0] w, input integer beat);
    reg [31:0] x;
    begin
        x = {w, 2'(beat), 8'(chip)} * 32'h9E3779B1;
        vp_seed = x[31:16] ^ x[15:0];
    end
endfunction
