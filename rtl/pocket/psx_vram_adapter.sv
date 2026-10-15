// SPDX-License-Identifier: MIT
// DDR-style PSX VRAM port -> normalized transactions. The original DDR port
// uses BYTE addresses and repeats DIN for writes with BURSTCNT > 1.
module psx_vram_adapter (
    input wire clk, reset,
    input wire rd, we, input wire [27:0] addr,
    input wire [7:0] count, be, input wire [63:0] din,
    output wire busy, output reg ready=0, output reg [63:0] dout=0,
    output wire req_valid, req_write, input wire req_ready,
    output wire [24:0] req_addr, output wire [3:0] req_count,
    output wire [127:0] req_data, output wire [15:0] req_be,
    input wire rsp_valid, rsp_error, input wire [127:0] rsp_data,
    output wire rsp_ready, output reg fault=0
);
    localparam IDLE=0, ISSUE=1, WAIT=2;
    reg [1:0] state=IDLE;
    reg write;
    reg [24:0] address;
    reg [7:0] remaining, mask;
    reg [63:0] data;
    assign busy=state!=IDLE;
    assign req_valid=state==ISSUE;
    assign req_write=write;
    assign req_addr=address;
    assign req_count=4;
    assign req_data={64'b0,data};
    assign req_be={8'b0,mask};
    assign rsp_ready=state==WAIT;
    always @(posedge clk) begin
        ready<=0;
        if (reset) begin state<=IDLE; fault<=0; end
        else case (state)
            IDLE: if (rd || we) begin
                write<=we; address<=addr[25:1]; remaining<=count;
                mask<=be; data<=din; state<=ISSUE;
                if (rd && we || count==0 || addr[2:0]!=0) fault<=1;
            end
            ISSUE: if (req_ready) state<=WAIT;
            WAIT: if (rsp_valid) begin
                if (rsp_error) fault<=1;
                if (!write) begin ready<=1; dout<=rsp_data[63:0]; end
                if (remaining<=1) state<=IDLE;
                else begin remaining<=remaining-1'b1; address<=address+4; state<=ISSUE; end
            end
        endcase
    end
endmodule
