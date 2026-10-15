`timescale 1ns/1ps
`default_nettype none
// Memory clocks only. The ratios and 4 ns SDRAM shift follow the pinned Pocket
// reference. CPU execution rate is not defined by this clock configuration.
module psx_mem_clocks (
    input wire clk_74a, reset_async,
    output wire clk_sys, clk_mem, clk_dram, locked
);
    altera_pll #(
        .fractional_vco_multiplier("true"), .reference_clock_frequency("74.25 MHz"),
        .operation_mode("normal"), .number_of_clocks(3),
        .output_clock_frequency0("62.500000 MHz"), .phase_shift0("0 ps"), .duty_cycle0(50),
        .output_clock_frequency1("125.000000 MHz"), .phase_shift1("0 ps"), .duty_cycle1(50),
        .output_clock_frequency2("125.000000 MHz"), .phase_shift2("4000 ps"), .duty_cycle2(50),
        .pll_type("General"), .pll_subtype("General")
    ) pll (
        .refclk(clk_74a), .rst(reset_async), .fbclk(1'b0), .fboutclk(),
        .outclk({clk_dram, clk_mem, clk_sys}), .locked(locked)
    );
endmodule
`default_nettype wire
