// SPDX-License-Identifier: MIT
// MiSTer spu_ram uses a 16-bit datum at an even byte address and BE=0011;
// only returned bits15:0 are consumed. The packet mailbox drains outside CE.
module psx_spu_sdram_adapter #(
    parameter [24:0] BASE_HALFWORD=25'h800000
) (
    input wire clk1x,mem_clk,reset_async,core_ce,discard_returns,
    input wire request,rnw,input wire [18:0] addr,
    input wire [31:0] data,input wire [3:0] byte_en,
    output wire done,output reg [31:0] read_data=0,
    output wire source_idle,output reg fault=0,
    output wire p_valid,p_write,input wire p_ready,
    output wire [24:0] p_addr,output wire [3:0] p_halfwords,
    output wire [127:0] p_wdata,output wire [15:0] p_byte_en,
    input wire p_rsp_valid,p_rsp_error,input wire [127:0] p_rsp_rdata,
    output wire p_rsp_ready
);
    localparam IDLE=0,ISSUE=1,WAIT=2,RETURN=3;
    reg [1:0] state=IDLE;
    reg write=0;reg [17:0] halfword=0;reg [15:0] word=0;reg [1:0] mask=0;
    wire ready,rvalid,rerror,src_reset,dst_reset;
    wire [127:0] rdata;
    assign source_idle=!src_reset && state==IDLE && ready;
    assign done=state==RETURN && core_ce && !discard_returns && !src_reset;
    psx_mem_cdc crossing (
        .src_clk(clk1x),.dst_clk(mem_clk),.reset_async(reset_async),.src_reset(src_reset),.dst_reset(dst_reset),
        .s_valid(state==ISSUE),.s_ready(ready),.s_write(write),.s_addr(BASE_HALFWORD+{7'b0,halfword}),
        .s_halfwords(4'd1),.s_wdata({112'b0,word}),.s_byte_en({14'b0,mask}),
        .s_rsp_valid(rvalid),.s_rsp_ready(state==WAIT),.s_rsp_rdata(rdata),.s_rsp_error(rerror),
        .d_valid(p_valid),.d_ready(p_ready),.d_write(p_write),.d_addr(p_addr),.d_halfwords(p_halfwords),
        .d_wdata(p_wdata),.d_byte_en(p_byte_en),.d_rsp_valid(p_rsp_valid),.d_rsp_ready(p_rsp_ready),
        .d_rsp_rdata(p_rsp_rdata),.d_rsp_error(p_rsp_error));
    always @(posedge clk1x) begin
        if(src_reset) begin state<=IDLE;write<=0;halfword<=0;word<=0;mask<=0;read_data<=0;fault<=0;end
        else begin
            if(core_ce && request && state!=IDLE) fault<=1;
            case(state)
                IDLE:if(core_ce && request) begin
                    write<=!rnw;halfword<=addr[18:1];word<=data[15:0];mask<=byte_en[1:0];state<=ISSUE;
                    if(addr[0] || byte_en[3:2]!=0) fault<=1;
                end
                ISSUE:if(ready) state<=WAIT;
                WAIT:if(rvalid) begin read_data<={16'b0,rdata[15:0]};state<=RETURN;if(rerror) fault<=1;end
                RETURN:if(core_ce || discard_returns) state<=IDLE;
                default:state<=IDLE;
            endcase
        end
    end
endmodule
