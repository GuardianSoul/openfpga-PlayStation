// SPDX-License-Identifier: MIT
// Simulation fixture: actual posted VRAM bridge/controller/read-retimed PHY.
// The pin model is generated from the existing strict model with a full,
// zero-initialized primary VRAM address window. No FPGA hardware is implied.
`timescale 1ns/1fs
module pocket_vram_native_fixture (
    input wire src_clk, reset_async,
    input wire rd, we,
    input wire [27:0] addr,
    input wire [7:0] count, be,
    input wire [63:0] din,
    output wire busy, ready, fault,
    output wire [63:0] dout,
    input wire [18:0] peek_addr,
    output wire [15:0] peek_data
);
    parameter real PIN_T_AC_SECOND=5.5;
    reg mem_clk=0, mem_clk2=1;
    always #8 mem_clk=~mem_clk;
    always #4 mem_clk2=~mem_clk2;
    wire mem_valid, mem_ready, mem_write, mem_rvalid, initialized;
    wire [23:0] mem_addr;
    wire [31:0] mem_wdata, mem_rdata;
    wire [3:0] mem_byte_en;
    psx_vram_stream stream (
        .src_clk(src_clk),.mem_clk(mem_clk),.reset_async(reset_async),.mem_initialized(initialized),
        .rd(rd),.we(we),.addr(addr),.count(count),.be(be),.din(din),
        .busy(busy),.ready(ready),.fault(fault),.dout(dout),
        .mem_valid(mem_valid),.mem_ready(mem_ready),.mem_write(mem_write),.mem_addr(mem_addr),
        .mem_wdata(mem_wdata),.mem_byte_en(mem_byte_en),.mem_rvalid(mem_rvalid),.mem_rdata(mem_rdata));
    wire rst=stream.mem_reset;
    wire [1:0] ras_n,cas_n,we_n;
    wire [25:0] address;
    wire [3:0] bank,mask;
    wire cke,wren,rden,rvalid;
    wire [31:0] wdata,rdata;
    sdr_ctrl #(.T_INIT(16000),.T_REFI(480),.RL(5),.AHEAD(0)) controller (
        .clk(mem_clk),.rst(rst),.ready(initialized),.req_valid(mem_valid),.req_ready(mem_ready),
        .req_we(mem_write),.req_addr(mem_addr),.req_wdata(mem_wdata),.req_be(mem_byte_en),.req_tag(3'b0),
        .la_valid(1'b0),.la_addr(24'b0),.rd_valid(mem_rvalid),.rd_data(mem_rdata),.rd_tag(),.rd_next(),.rd_next_tag(),
        .dfi_ras_n(ras_n),.dfi_cas_n(cas_n),.dfi_we_n(we_n),.dfi_addr(address),.dfi_ba(bank),.dfi_cke(cke),
        .dfi_wrdata_en(wren),.dfi_wrdata(wdata),.dfi_wrdata_mask(mask),.dfi_rddata_en(rden),
        .dfi_rddata(rdata),.dfi_rddata_valid(rvalid));
    reg [2:0] phy_reset=7;
    always @(posedge mem_clk2 or posedge reset_async)
        if(reset_async) phy_reset<=7;
        else phy_reset<={phy_reset[1:0],1'b0};
    wire [12:0] a;
    wire [1:0] ba,dm;
    wire ras,cas,we_pin,cke_pin,dram_clk;
    wire [15:0] dq;
    psx_sdr_phy phy (.clk(mem_clk),.rst(rst),.clk2x(mem_clk2),.rst2x(phy_reset[2]),.clk2x_90(!mem_clk2),
        .ras_n(ras_n),.cas_n(cas_n),.we_n(we_n),.addr(address),.ba(bank),.cke(cke),
        .wrdata_en(wren),.wrdata(wdata),.wrdata_mask(mask),.rddata_en(rden),
        .rddata(rdata),.rddata_valid(rvalid),.sdram_a(a),.sdram_ba(ba),.sdram_dm(dm),
        .sdram_dq(dq),.sdram_ras_n(ras),.sdram_cas_n(cas),.sdram_we_n(we_pin),
        .sdram_cke(cke_pin),.sdram_clock(dram_clk));
    gpu_sdr_pin_model #(.T_AC(5.5),.T_AC_SECOND(PIN_T_AC_SECOND),.T_HZ(6.0)) memory (
        .clk(dram_clk),.cke(cke_pin),.ras_n(ras),.cas_n(cas),.we_n(we_pin),
        .address(a),.bank(ba),.dm(dm),.host_oe(phy.oe_reg),.dq(dq));
    assign peek_data=memory.memory[peek_addr];
endmodule
