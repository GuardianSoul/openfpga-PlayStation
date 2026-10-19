// SPDX-License-Identifier: MIT
// One ordered 64-bit shadow access over the controller's dedicated 32-bit
// PGXP client. Reset is the physical controller's init, NOT the guest reset:
// an accepted access drains even if the guest cancels its precision work.
`default_nettype none
module psx_pgxp_sdram64 (
    input wire clk, init,
    input wire valid,
    output wire ready,
    input wire write_access,
    input wire [24:0] address,
    input wire [63:0] data,
    output reg done=0,
    output reg [63:0] q=0,
    output reg ch_req=0, ch_rnw=0,
    output reg [26:0] ch_address=0,
    output reg [31:0] ch_data=0,
    output wire [3:0] ch_mask,
    input wire ch_done,
    input wire [31:0] ch_q
);
    localparam IDLE=0, LOW_WORD=1, HIGH_WORD=2;
    reg [1:0] state=IDLE;
    reg [31:0] upper_write=0,lower_read=0;
    assign ready=state==IDLE && !init;
    assign ch_mask=4'hf;
    always @(posedge clk) begin
        ch_req<=0;done<=0;
        if(init) begin state<=IDLE;ch_req<=0;done<=0;end
        else case(state)
            IDLE: if(valid && ready)begin
                // synthesis translate_off
                assert(address[2:0]==0)else $fatal(1,"Unaligned PGXP shadow record");
                // synthesis translate_on
                ch_address<={2'd0,address};ch_data<=data[31:0];upper_write<=data[63:32];
                ch_rnw<=!write_access;ch_req<=1;state<=LOW_WORD;
            end
            LOW_WORD: if(ch_done)begin
                lower_read<=ch_q;ch_address<=ch_address+27'd4;
                ch_data<=upper_write;ch_req<=1;state<=HIGH_WORD;
            end
            HIGH_WORD: if(ch_done)begin
                if(ch_rnw)q<={ch_q,lower_read};
                done<=1;state<=IDLE;
            end
            default:state<=IDLE;
        endcase
    end
endmodule
`default_nettype wire
