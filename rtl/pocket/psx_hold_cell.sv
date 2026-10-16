// SPDX-License-Identifier: MIT
// Identity LUT for a measured, synchronous data-path experiment.
// No fixed propagation delay is assumed; every setup/hold corner must pass.
module psx_hold_cell(input wire i, output wire o);
    cyclonev_lcell_comb #(
        .lut_mask(64'hAAAAAAAAAAAAAAAA), .dont_touch("on")
    ) identity_lut (
        .dataa(i), .combout(o)
    );
endmodule
