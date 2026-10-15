`timescale 1ns/1ps
// Authored CPU diagnostic fixture, not the physical release top. Pin model
// storage/preload is supplied privately by run_native_sdram_system.py.
module native_sdram_memory (
    input wire clk1x,clk3x,core_ce,reset_async,guest_reset,
    input wire ram_ena,ram_rnw,ram_dma,ram_cache,
    input wire [24:0] ram_adr,input wire [1:0] ram_cntDMA,
    input wire [31:0] ram_dataWrite,input wire [3:0] ram_be,
    output wire ram_done,output wire [31:0] ram_dataRead32,
    output wire dma_wr,output wire [31:0] dma_data,output wire dma_reqprocessed,
    output wire [3:0] cache_wr,output wire [31:0] cache_data,output wire [7:0] cache_addr,
    input wire [22:0] ram_dmafifo_adr,input wire [31:0] ram_dmafifo_data,
    input wire ram_dmafifo_empty,output wire ram_dmafifo_read
);
    reg clk=0,clk_mem=1;
    always #8 clk=~clk;
    always #4 clk_mem=~clk_mem;
    wire clk_dram=~clk_mem;
    wire initialized,client_reset,source_idle,service_idle,adapter_fault;
    wire av,aw,ar,arv,are,arr;
    wire [24:0] aa;wire [3:0] an;wire [127:0] ad,ard;wire [15:0] ab;
    reg drop_final_write=0;
    initial drop_final_write=$test$plusargs("drop_final_write");
    // Fault fixture: acknowledge the final offered store but mask its chip
    // bytes. The independent committed-memory check must reject it.
    wire [15:0] backend_be=drop_final_write && aw && aa==25'h7f80 ? 16'b0:ab;
    psx_mister_main_adapter adapter (
        .clk1x(clk1x),.clk3x(clk3x),.mem_clk(clk),.reset_async(reset_async),
        .core_ce(core_ce),.discard_returns(guest_reset),
        .ram_ena(ram_ena),.ram_rnw(ram_rnw),.ram_dma(ram_dma),.ram_cache(ram_cache),
        .ram_adr(ram_adr),.ram_cntDMA(ram_cntDMA),.ram_dataWrite(ram_dataWrite),.ram_be(ram_be),
        .ram_done(ram_done),.ram_dataRead32(ram_dataRead32),.dma_wr(dma_wr),.dma_data(dma_data),
        .dma_reqprocessed(dma_reqprocessed),.cache_wr(cache_wr),.cache_data(cache_data),.cache_addr(cache_addr),
        .ram_dmafifo_adr(ram_dmafifo_adr),.ram_dmafifo_data(ram_dmafifo_data),
        .ram_dmafifo_empty(ram_dmafifo_empty),.ram_dmafifo_read(ram_dmafifo_read),
        .source_idle(source_idle),.service_idle(service_idle),.fault(adapter_fault),
        .p_valid(av),.p_write(aw),.p_ready(ar),.p_addr(aa),.p_halfwords(an),
        .p_wdata(ad),.p_byte_en(ab),.p_rsp_valid(arv),.p_rsp_error(are),.p_rsp_rdata(ard),.p_rsp_ready(arr));
    wire [12:0] a;wire [1:0] ba,dm;wire ras_n,cas_n,we_n,cke,dram_clk;
    wire [15:0] dq;
    psx_sdram_pins #(.T_REFI(240)) pads (
        .clk_sys(clk),.clk_mem(clk_mem),.clk_dram(clk_dram),.reset_async(reset_async),
        .initialized(initialized),.client_reset(client_reset),
        .req_valid(av),.req_ready(ar),.req_write(aw),.req_addr(aa),.req_halfwords(an),
        .req_wdata(ad),.req_byte_en(backend_be),.rsp_valid(arv),.rsp_ready(arr),.rsp_rdata(ard),.rsp_error(are),
        .dram_a(a),.dram_ba(ba),.dram_dqm(dm),.dram_ras_n(ras_n),.dram_cas_n(cas_n),
        .dram_we_n(we_n),.dram_cke(cke),.dram_dq(dq),.dram_clk(dram_clk));
    sdr_pin_model chip (.clk(dram_clk),.cke(cke),.ras_n(ras_n),.cas_n(cas_n),.we_n(we_n),
        .address(a),.bank(ba),.dm(dm),.host_oe(pads.phy.oe_reg),.dq(dq));
    // Observe actual DFI/PHY write stages, not a fixed delay after a response.
    // A production ownership interface still needs synthesizable side ports.
    wire physical_write_idle=!pads.memory.dfi_wrdata_en && !pads.phy.r_wrdata_en &&
        !pads.phy.wr_en_d && !pads.phy.oe_reg;
    wire fabric_idle=initialized && ar && !av && !arv && physical_write_idle;
    integer packets=0,cache_words=0,dma_words=0;
    always @(posedge clk) if(!reset_async) begin
        if(av && ar) packets++;
        if(adapter_fault || are && arv) $fatal(1,"Native SDRAM CPU memory fault");
    end
    always @(posedge clk3x) if(cache_wr!=0) cache_words++;
    always @(posedge clk1x) if(dma_wr) dma_words++;
endmodule
