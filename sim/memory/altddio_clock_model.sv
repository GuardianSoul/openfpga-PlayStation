`timescale 1ns/1ps
`default_nettype none
// Simulation-only model of the DDR clock output used by the SDRAM PHY.
// This covers logical edge forwarding, not vendor primitive electrical timing.
module ALTDDIO_OUT #(parameter integer WIDTH = 1) (
    input wire [WIDTH-1:0] datain_h, datain_l,
    input wire outclock,
    output wire [WIDTH-1:0] dataout
);
    assign dataout = outclock ? datain_h : datain_l;
endmodule
`default_nettype wire
