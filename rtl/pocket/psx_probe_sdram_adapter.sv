// SPDX-License-Identifier: MIT
// Serialized loader/readback to the pinned MiSTer SDRAM channel 3.
module psx_probe_sdram_adapter (
    input wire clk, reset, req_valid, req_write,
    output wire req_ready,
    input wire [24:0] req_addr, input wire [127:0] req_data,
    output wire rsp_valid, input wire rsp_ready,
    output wire [127:0] rsp_data, output wire rsp_error,
    output reg request=0, output reg rnw=1,
    output reg [26:0] address=0, output reg [31:0] data=0,
    input wire done, input wire [31:0] read_data
);
    reg [1:0] state=0;
    reg [31:0] result=0;
    assign req_ready=state==0;
    assign rsp_valid=state==2;
    assign rsp_data={96'b0,result}; assign rsp_error=0;
    always @(posedge clk) begin
        request<=0;
        if (reset) state<=0;
        else case(state)
            0: if(req_valid) begin
                // Region zero BIOS at the original physical 8 MiB offset.
                address<=27'h0800000+{1'b0,req_addr,1'b0}; data<=req_data[31:0];
                rnw<=!req_write; request<=1; state<=1;
            end
            1: if(done) begin result<=read_data; state<=2; end
            2: if(rsp_ready) state<=0;
        endcase
    end
endmodule
