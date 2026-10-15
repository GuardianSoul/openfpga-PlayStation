// SPDX-License-Identifier: MIT
// Independent digital button levels, synchronized into the free-running CPU
// clock. This is not an atomic CDC for a numeric/encoded multi-bit value.
module psx_pocket_pad (
    input wire clk1x,reset_n,input wire [15:0] apf_keys,
    output wire [15:0] buttons
);
    (* async_reg="true", preserve, dont_merge *) reg [15:0] keys_meta=0,keys_cpu=0;
    always @(posedge clk1x or negedge reset_n) begin
        if(!reset_n) begin keys_meta<=0;keys_cpu<=0;end
        else begin keys_meta<=apf_keys;keys_cpu<=keys_meta;end
    end
    assign buttons=keys_cpu;
endmodule
