`timescale 1ns/1ps
`default_nettype none
// Simulation-only nominal clocks/lock delay. Does not model Intel PLL internals,
// fractional jitter, lock loss, physical routing or frequency tolerance.
module altera_pll #(
    parameter fractional_vco_multiplier = "true", reference_clock_frequency = "74.25 MHz",
    parameter operation_mode = "normal", number_of_clocks = 3,
    parameter output_clock_frequency0 = "62.500000 MHz", phase_shift0 = "0 ps", duty_cycle0 = 50,
    parameter output_clock_frequency1 = "125.000000 MHz", phase_shift1 = "0 ps", duty_cycle1 = 50,
    parameter output_clock_frequency2 = "125.000000 MHz", phase_shift2 = "4000 ps", duty_cycle2 = 50,
    parameter pll_type = "General", pll_subtype = "General"
) (
    input wire refclk, rst, fbclk,
    output wire fboutclk,
    output wire [2:0] outclk,
    output reg locked = 0
);
    reg sys = 0, mem = 1;
    always #8 sys = ~sys;
    always #4 mem = ~mem;
    assign outclk = {~mem, mem, sys};
    assign fboutclk = 0;
    integer lock_count = 0;
    always @(posedge refclk or posedge rst)
        if (rst) begin locked <= 0; lock_count <= 0; end
        else if (lock_count < 4) lock_count <= lock_count + 1;
        else locked <= 1;
    initial if (number_of_clocks != 3 || output_clock_frequency0 != "62.500000 MHz" ||
        output_clock_frequency1 != "125.000000 MHz" || output_clock_frequency2 != "125.000000 MHz" ||
        phase_shift0 != "0 ps" || phase_shift1 != "0 ps" || phase_shift2 != "4000 ps" ||
        reference_clock_frequency != "74.25 MHz" || duty_cycle0 != 50 || duty_cycle1 != 50 || duty_cycle2 != 50)
        $fatal(1, "PLL model does not cover this configuration");
endmodule
`default_nettype wire
