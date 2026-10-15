// SPDX-License-Identifier: MIT
// Two packet sources: 0=guest DDR-window adapter; 1=APF card transfer engine.
// Guest adapter queues must be empty before guest_stopped is asserted. The
// CDC's outstanding request/response must also drain, even while guest CE=0.
// backend_idle includes controller/pin idle; a packet response alone is not
// sufficient to change owners. Common reset is required in every domain.
module psx_memcard_fabric (
    input wire guest_clk,host_clk,memory_clk,reset,
    input wire host_freeze,guest_stopped,backend_idle,
    output wire guest_pause,output wire host_owned,
    input wire [1:0] s_valid,s_write,output wire [1:0] s_ready,
    input wire [1:0][24:0] s_addr,input wire [1:0][3:0] s_halfwords,
    input wire [1:0][127:0] s_wdata,input wire [1:0][15:0] s_byte_en,
    output wire [1:0] s_rsp_valid,input wire [1:0] s_rsp_ready,
    output wire [1:0][127:0] s_rsp_rdata,output wire [1:0] s_rsp_error,
    output wire req_valid,req_write,input wire req_ready,
    output wire [24:0] req_addr,output wire [3:0] req_halfwords,
    output wire [127:0] req_wdata,output wire [15:0] req_byte_en,
    input wire rsp_valid,rsp_error,input wire [127:0] rsp_rdata,
    output wire rsp_ready,protocol_error
);
    wire [1:0] source_clocks={host_clk,guest_clk};
    wire [1:0] p_valid,p_write,p_ready,p_rsp_valid,p_rsp_ready,p_rsp_error;
    wire [1:0][24:0] p_addr;wire [1:0][3:0] p_halfwords;
    wire [1:0][127:0] p_wdata,p_rsp_rdata;wire [1:0][15:0] p_byte_en;
    for(genvar i=0;i<2;i=i+1) begin: crossing
        psx_mem_cdc cdc(.src_clk(source_clocks[i]),.dst_clk(memory_clk),.reset_async(reset),
            .s_valid(s_valid[i]),.s_write(s_write[i]),.s_ready(s_ready[i]),.s_addr(s_addr[i]),
            .s_halfwords(s_halfwords[i]),.s_wdata(s_wdata[i]),.s_byte_en(s_byte_en[i]),
            .s_rsp_valid(s_rsp_valid[i]),.s_rsp_ready(s_rsp_ready[i]),
            .s_rsp_rdata(s_rsp_rdata[i]),.s_rsp_error(s_rsp_error[i]),
            .d_valid(p_valid[i]),.d_write(p_write[i]),.d_ready(p_ready[i]),.d_addr(p_addr[i]),
            .d_halfwords(p_halfwords[i]),.d_wdata(p_wdata[i]),.d_byte_en(p_byte_en[i]),
            .d_rsp_valid(p_rsp_valid[i]),.d_rsp_ready(p_rsp_ready[i]),
            .d_rsp_rdata(p_rsp_rdata[i]),.d_rsp_error(p_rsp_error[i]));
    end
    (* async_reg="true", preserve, dont_merge *) reg pause_meta=0,pause_guest=0;
    (* async_reg="true", preserve, dont_merge *) reg freeze_meta=0,freeze_memory=0;
    (* async_reg="true", preserve, dont_merge *) reg stopped_meta=0,stopped_memory=0;
    (* async_reg="true", preserve, dont_merge *) reg host_idle_meta=0,host_idle_memory=0;
    (* async_reg="true", preserve, dont_merge *) reg owned_meta=0,owned_host=0;
    reg owned=0;
    wire guest_drained=guest_stopped && pause_guest && s_ready[0] && !s_valid[0] && !s_rsp_valid[0];
    wire host_drained=s_ready[1] && !s_valid[1] && !s_rsp_valid[1];
    assign guest_pause=pause_guest;
    assign host_owned=owned_host;
    always @(posedge guest_clk or posedge reset)
        if(reset) begin pause_meta<=0;pause_guest<=0;end
        else begin pause_meta<=host_freeze;pause_guest<=pause_meta;end
    always @(posedge host_clk or posedge reset)
        if(reset) begin owned_meta<=0;owned_host<=0;end
        else begin owned_meta<=owned;owned_host<=owned_meta;end
    always @(posedge memory_clk or posedge reset) begin
        if(reset) begin
            freeze_meta<=0;freeze_memory<=0;stopped_meta<=0;stopped_memory<=0;
            host_idle_meta<=0;host_idle_memory<=0;
        end else begin
            freeze_meta<=host_freeze;freeze_memory<=freeze_meta;
            stopped_meta<=guest_drained;stopped_memory<=stopped_meta;
            host_idle_meta<=host_drained;host_idle_memory<=host_idle_meta;
        end
    end
    wire [1:0] enabled=owned ? 2'b10:2'b01;
    psx_mem_arb #(.PORTS(2)) arb(.clk(memory_clk),.rst(reset),
        .p_valid(p_valid & enabled),.p_write(p_write),.p_ready(p_ready),
        .p_addr(p_addr),.p_halfwords(p_halfwords),.p_wdata(p_wdata),.p_byte_en(p_byte_en),
        .p_rsp_valid(p_rsp_valid),.p_rsp_ready(p_rsp_ready),.p_rsp_rdata(p_rsp_rdata),.p_rsp_error(p_rsp_error),
        .req_valid(req_valid),.req_ready(req_ready),.req_write(req_write),.req_addr(req_addr),
        .req_halfwords(req_halfwords),.req_wdata(req_wdata),.req_byte_en(req_byte_en),
        .rsp_valid(rsp_valid),.rsp_ready(rsp_ready),.rsp_rdata(rsp_rdata),.rsp_error(rsp_error),
        .protocol_error(protocol_error));
    wire route_idle=backend_idle && !req_valid && !(|p_rsp_valid) && !(|(p_valid & enabled));
    always @(posedge memory_clk) begin
        if(reset) owned<=0;
        else if(!owned && freeze_memory && stopped_memory && route_idle) owned<=1;
        else if(owned && !freeze_memory && host_idle_memory && route_idle) owned<=0;
    end
endmodule
