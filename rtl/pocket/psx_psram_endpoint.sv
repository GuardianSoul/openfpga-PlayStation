// SPDX-License-Identifier: MIT
// One 64-bit normalized transaction to MIRLO's paired 32-bit burst port.
// Requests must be naturally aligned and cannot cross a 128-word row.
module psx_psram_endpoint (
    input wire clk, reset,
    input wire req_valid, req_write, output wire req_ready,
    input wire [24:0] req_addr, input wire [127:0] req_data,
    input wire [15:0] req_be,
    output wire rsp_valid, input wire rsp_ready,
    output reg [127:0] rsp_data=0, output wire rsp_error,
    output wire z_valid, z_we, z_last, input wire z_ready, z_rvalid, z_idle,
    output wire [21:0] z_addr, output wire [31:0] z_data, input wire [31:0] z_read,
    output wire [3:0] z_mask
);
    localparam IDLE=0, SEND=1, WAIT=2, RESPONSE=3;
    reg [1:0] state=IDLE;
    reg write, sent=0, received=0;
    reg [21:0] address;
    reg [63:0] data;
    reg [7:0] mask;
    assign req_ready=state==IDLE;
    assign rsp_valid=state==RESPONSE;
    assign rsp_error=0;
    assign z_valid=state==SEND;
    assign z_we=write;
    assign z_last=sent;
    assign z_addr=address;
    assign z_data=sent ? data[63:32] : data[31:0];
    assign z_mask=sent ? mask[7:4] : mask[3:0];
    always @(posedge clk) begin
        if (reset) state<=IDLE;
        else case(state)
            IDLE: if (req_valid) begin
                address<=req_addr[22:1]; write<=req_write; data<=req_data[63:0]; mask<=req_be[7:0];
                sent<=0; received<=0; rsp_data<=0; state<=SEND;
            end
            SEND: if (z_ready) begin
                if (sent) state<=WAIT;
                else sent<=1;
            end
            WAIT: if (write && z_idle) state<=RESPONSE;
            RESPONSE: if (rsp_ready) state<=IDLE;
        endcase
        if (!reset && z_rvalid && !write && (state==SEND || state==WAIT)) begin
            if (!received) begin rsp_data[31:0]<=z_read; received<=1; end
            else begin rsp_data[63:32]<=z_read; state<=RESPONSE; end
        end
    end
endmodule
