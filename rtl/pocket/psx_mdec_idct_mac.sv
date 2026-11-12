// SPDX-License-Identifier: GPL-2.0-only
// Experimental replacement for the selected MDEC's product and sum stages.
// Inputs are its existing P-stage operands; no additional clock edge is added.
module psx_mdec_idct_mac (
    input wire clk, enable, valid, first,
    input wire signed [15:0] a0, b0, a1, b1,
    output wire [30:0] sum
);
    altera_mult_add #(
        .selected_device_family("Cyclone V"),
        .dedicated_multiplier_circuitry("YES"),
        .number_of_multipliers(2), .width_a(16), .width_b(16),
        .width_result(31), .representation_a("SIGNED"),
        .representation_b("SIGNED"),
        // Retiming the old product stage to its operands preserves its edge
        // while leaving the hard multiplier -> adder -> accumulator connected.
        .input_register_a0("CLOCK0"), .input_register_b0("CLOCK0"),
        .input_register_a1("CLOCK0"), .input_register_b1("CLOCK0"),
        .multiplier_register0("UNREGISTERED"),
        .multiplier_register1("UNREGISTERED"),
        .output_register("CLOCK1"),
        .accumulator("YES"), .accum_direction("ADD"),
        .loadconst_value(14), .use_sload_accum_port("NO")
    ) mac (
        .dataa({a1, a0}), .datab({b1, b0}), .result(sum),
        .clock0(clk), .clock1(clk),
        .ena0(enable), .ena1(enable && valid),
        .aclr0(1'b0), .aclr1(1'b0), .aclr2(1'b0), .aclr3(1'b0),
        .sclr0(1'b0), .sclr1(1'b0), .sclr2(1'b0), .sclr3(1'b0),
        .accum_sload(first), .sload_accum(1'b0),
        .signa(1'b1), .signb(1'b1),
        .addnsub1(1'b1), .addnsub3(1'b1),
        .zero_loopback(1'b0), .zero_chainout(1'b0), .chainin(1'b0),
        .negate(1'b0), .rotate(1'b0), .shift_right(1'b0)
    );
endmodule
