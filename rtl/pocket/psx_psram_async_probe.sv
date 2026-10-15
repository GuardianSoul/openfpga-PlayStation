// SPDX-License-Identifier: MIT
// Asynchronous PSRAM baseline for hardware bring-up: drives MIRLO64 psram's
// X port (die 1, single asynchronous words). Writes 256 words at the start
// and 256 at the end of the die, all before any read, then reads all back.
`default_nettype none
module psx_psram_async_probe (
    input wire clk, reset, ready,
    output reg x_valid, x_we,
    output reg [21:0] x_addr,
    output reg [31:0] x_wdata,
    input wire x_ack,
    input wire [31:0] x_rdata,
    output reg passed, failed,
    output reg [15:0] checked,
    output reg [21:0] failed_address,
    output reg [31:0] failed_expected, failed_actual
);
    function automatic [31:0] pattern(input [21:0] a);
        pattern=32'h6c3a95e1 ^ {a[9:0],a} ^ {a[5:0],26'b0};
    endfunction
    reg [8:0] index;
    reg reading;
    wire [21:0] address={index[8] ? 14'h3fff : 14'h0000, index[7:0]};
    reg [23:0] watchdog;
    always @(posedge clk) begin
        if (reset) begin
            x_valid<=0; x_we<=0; x_addr<=0; x_wdata<=0; index<=0; reading<=0;
            passed<=0; failed<=0; checked<=0; watchdog<=0;
            failed_address<=0; failed_expected<=0; failed_actual<=0;
        end else if (ready && !passed && !failed) begin
            watchdog<=watchdog+1'b1;
            if (&watchdog) failed<=1;
            if (!x_valid) begin
                x_valid<=1; x_we<=!reading; x_addr<=address; x_wdata<=pattern(address);
            end else if (x_ack) begin
                x_valid<=0; watchdog<=0;
                if (reading) begin
                    if (x_rdata!==pattern(x_addr)) begin
                        failed<=1; failed_address<=x_addr; failed_expected<=pattern(x_addr); failed_actual<=x_rdata;
                    end else checked<=checked+1'b1;
                end
                index<=index+1'b1;
                if (&index) begin
                    if (reading) passed<=x_rdata===pattern(x_addr);
                    reading<=1;
                end
            end
        end
    end
endmodule
`default_nettype wire
