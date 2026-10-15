// SPDX-License-Identifier: MIT
// Serves MIRLO64 psram's burst (Z) port contract through its asynchronous
// (X) port, one 32-bit word per access, on die 1. Hardware bring-up on
// the test Pocket: asynchronous PSRAM passes, synchronous bursts do not yet
// (docs/pocket-sweep-probe.md). Words of one Z run go to consecutive
// addresses from z_addr, as the burst port's would. Read data returns in
// order with z_rvalid; z_idle drops while an access is pending.
`default_nettype none
module psx_psram_z2x (
    input wire clk, reset, ready,
    input wire z_valid, z_we, z_last,
    output wire z_ready,
    input wire [21:0] z_addr,
    input wire [31:0] z_data,
    input wire [3:0] z_mask,
    output reg z_rvalid,
    output reg [31:0] z_read,
    output wire z_idle,
    output reg x_valid, x_we,
    output reg [21:0] x_addr,
    output reg [31:0] x_wdata,
    output reg [3:0] x_be,
    input wire x_ack,
    input wire [31:0] x_rdata
);
    reg busy, mid;
    assign z_ready=ready && !busy;
    assign z_idle=!busy;
    wire take=z_valid && z_ready;
    always @(posedge clk) begin
        z_rvalid<=0;
        if (reset) begin
            busy<=0; mid<=0; x_valid<=0; x_we<=0; x_addr<=0; x_wdata<=0; x_be<=0; z_read<=0;
        end else if (take) begin
            busy<=1; x_valid<=1; x_we<=z_we; x_wdata<=z_data; x_be<=z_we ? z_mask : 4'hf;
            x_addr<=mid ? x_addr+1'b1 : z_addr;
            mid<=!z_last;
        end else if (busy && x_ack) begin
            busy<=0; x_valid<=0;
            if (!x_we) begin z_rvalid<=1; z_read<=x_rdata; end
        end
    end
endmodule
`default_nettype wire
