`timescale 1ns/1ps
`default_nettype none
// Simulation-only DDR output registers. Both halves are captured at the
// rising clock edge; the stored lower half is driven on the falling edge.
// Unlike the constant-clock forwarding fixture, data can change mid-cycle.
// Covers normal enabled operation, default low power-up, no reset/enable IP.
module ALTDDIO_OUT #(parameter integer WIDTH=1)(
    input wire [WIDTH-1:0] datain_h,datain_l,
    input wire outclock,
    output reg [WIDTH-1:0] dataout=0
);
    reg [WIDTH-1:0] low_half=0;
    always @(posedge outclock) begin low_half<=datain_l;dataout<=datain_h;end
    always @(negedge outclock) dataout<=low_half;
endmodule
`default_nettype wire
