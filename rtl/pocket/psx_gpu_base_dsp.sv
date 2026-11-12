// SPDX-License-Identifier: GPL-2.0-only
// Low20 of the native accumulator preserve the polygon's modular arithmetic.
module psx_gpu_base_dsp (
    input wire clk, enable, load, initialize,
    input wire [7:0] vertex,
    input wire [19:0] increment,
    output wire [19:0] value
);
    // Each ordinary load is vertex*4096 + 2048. Cold initialization supplies
    // -2048 to cancel the same hard constant; warm reset does not clear base.
    wire signed [19:0] operand = initialize ? -20'sd2048
                               : load ? {vertex,12'b0} : increment;
    wire [63:0] accumulator;
    assign value = accumulator[19:0];
    altera_mult_add #(
        .selected_device_family("Cyclone V"),
        .dedicated_multiplier_circuitry("YES"),
        .number_of_multipliers(1), .width_a(20), .width_b(2),
        .width_result(64), .representation_a("SIGNED"), .representation_b("SIGNED"),
        .accumulator("YES"), .accum_direction("ADD"),
        .loadconst_value(11), .use_sload_accum_port("YES"),
        .output_register("CLOCK0")
    ) base (
        .dataa(operand), .datab(2'sd1), .result(accumulator),
        .clock0(clk), .ena0(enable || initialize),
        .sload_accum(!(load || initialize)), .accum_sload(1'b0),
        .aclr0(1'b0), .aclr1(1'b0), .aclr2(1'b0), .aclr3(1'b0),
        .sclr0(1'b0), .sclr1(1'b0), .sclr2(1'b0), .sclr3(1'b0),
        .signa(1'b1), .signb(1'b1), .addnsub1(1'b1), .addnsub3(1'b1),
        .zero_loopback(1'b0), .zero_chainout(1'b0), .chainin(1'b0),
        .negate(1'b0), .rotate(1'b0), .shift_right(1'b0)
    );
endmodule
