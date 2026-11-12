// SPDX-License-Identifier: GPL-2.0-only
// Original modulo-31-bit four-product sum, without a pipeline change.
module psx_spu_gaussian_sum (
    input wire signed [15:0] x0, x1, x2, x3,
    input wire signed [15:0] c0, c1, c2, c3,
    output wire [30:0] sum
);
    altera_mult_add #(
        .selected_device_family("Cyclone V"),
        .dedicated_multiplier_circuitry("YES"),
        .number_of_multipliers(4), .width_a(16), .width_b(16),
        .width_result(31), .representation_a("SIGNED"),
        .representation_b("SIGNED")
    ) products (
        .dataa({x3,x2,x1,x0}), .datab({c3,c2,c1,c0}), .result(sum),
        .aclr0(1'b0), .aclr1(1'b0), .aclr2(1'b0), .aclr3(1'b0),
        .sclr0(1'b0), .sclr1(1'b0), .sclr2(1'b0), .sclr3(1'b0),
        .signa(1'b1), .signb(1'b1), .addnsub1(1'b1), .addnsub3(1'b1),
        .zero_loopback(1'b0), .zero_chainout(1'b0), .chainin(1'b0),
        .negate(1'b0), .rotate(1'b0), .shift_right(1'b0),
        .accum_sload(1'b0), .sload_accum(1'b0)
    );
endmodule
