// SPDX-License-Identifier: MIT
// Memory-clock-domain CPU/SPU/CD/BIOS packet routing. Source adapters/CDC must
// honor the normalized ready/valid contract and quiesce independently of CE.
// Physical address translation belongs to those adapters, not this fabric.
// boot_request and guest_quiesced MUST already be synchronous to clk.
module psx_main_memory (
    input wire clk,reset,initialized,boot_request,guest_quiesced,
    output reg boot_owned=0,output wire idle,protocol_error,
    input wire [3:0] p_valid,p_write,output wire [3:0] p_ready,
    input wire [3:0][24:0] p_addr,input wire [3:0][3:0] p_halfwords,
    input wire [3:0][127:0] p_wdata,input wire [3:0][15:0] p_byte_en,
    output wire [3:0] p_rsp_valid,input wire [3:0] p_rsp_ready,
    output wire [3:0][127:0] p_rsp_rdata,output wire [3:0] p_rsp_error,
    output wire z_valid,z_we,z_die,z_last,input wire z_ready,z_rvalid,z_idle,wait_error,
    output wire [21:0] z_addr,output wire [31:0] z_wdata,
    output wire [3:0] z_wmask,input wire [31:0] z_rdata
);
    wire [3:0] enabled=boot_owned ? 4'b1000:4'b0111;
    wire req_valid,req_ready,req_write,rsp_valid,rsp_ready,rsp_error;
    wire [24:0] req_addr;wire [3:0] req_halfwords;
    wire [127:0] req_wdata,rsp_rdata;wire [15:0] req_byte_en;
    wire packet_idle,packet_fault,arb_error;
    psx_mem_arb #(.PORTS(4)) arb (
        .clk(clk),.rst(reset),.p_valid(p_valid & enabled),.p_write(p_write),.p_ready(p_ready),
        .p_addr(p_addr),.p_halfwords(p_halfwords),.p_wdata(p_wdata),.p_byte_en(p_byte_en),
        .p_rsp_valid(p_rsp_valid),.p_rsp_ready(p_rsp_ready),.p_rsp_rdata(p_rsp_rdata),
        .p_rsp_error(p_rsp_error),.req_valid(req_valid),.req_ready(req_ready),
        .req_write(req_write),.req_addr(req_addr),.req_halfwords(req_halfwords),
        .req_wdata(req_wdata),.req_byte_en(req_byte_en),.rsp_valid(rsp_valid),
        .rsp_ready(rsp_ready),.rsp_rdata(rsp_rdata),.rsp_error(rsp_error),.protocol_error(arb_error));
    psx_psram_packet packet (
        .clk(clk),.reset(reset),.initialized(initialized),.req_valid(req_valid),.req_ready(req_ready),
        .req_write(req_write),.req_addr(req_addr),.req_halfwords(req_halfwords),
        .req_wdata(req_wdata),.req_byte_en(req_byte_en),.rsp_valid(rsp_valid),.rsp_ready(rsp_ready),
        .rsp_rdata(rsp_rdata),.rsp_error(rsp_error),.idle(packet_idle),.fault(packet_fault),
        .z_valid(z_valid),.z_we(z_we),.z_die(z_die),.z_last(z_last),.z_ready(z_ready),
        .z_rvalid(z_rvalid),.z_idle(z_idle),.wait_error(wait_error),
        .z_addr(z_addr),.z_wdata(z_wdata),.z_wmask(z_wmask),.z_rdata(z_rdata));
    assign protocol_error=arb_error || packet_fault;
    assign idle=packet_idle && !req_valid && p_rsp_valid==0 && (p_valid & enabled)==0;
    // Leave guest routing connected throughout drain. An offered-but-stalled
    // packet, an unconsumed client response, or physical pin work blocks handoff.
    always @(posedge clk) begin
        if(reset) boot_owned<=0;
        else if(!boot_owned && boot_request && guest_quiesced && idle) boot_owned<=1;
        else if(boot_owned && !boot_request && idle) boot_owned<=0;
    end
endmodule
