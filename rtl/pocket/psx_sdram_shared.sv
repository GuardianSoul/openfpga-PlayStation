// SPDX-License-Identifier: MIT
// Four ordered 32-bit clients share the original MIRLO SDRAM controller/PHY.
// All client ports use clk_sys; CDC and packet completion belong to callers.
// The controller's read tags route interleaved pipelined returns. Writes are
// acknowledged at command acceptance; backend_idle includes physical drain.
module psx_sdram_shared #(
    parameter integer T_INIT=12600,T_REFI=240
) (
    input wire clk_sys,clk_mem,clk_dram,reset_async,
    output wire initialized,client_reset,backend_idle,
    output reg fault=0,
    input wire [3:0] valid,write,
    output wire [3:0] ready,
    input wire [3:0][23:0] addr,
    input wire [3:0][31:0] data,
    input wire [3:0][3:0] byte_en,
    output wire [3:0] rvalid,
    output wire [31:0] rdata,
    output wire [12:0] dram_a,output wire [1:0] dram_ba,dram_dqm,
    output wire dram_ras_n,dram_cas_n,dram_we_n,dram_cke,
    inout wire [15:0] dram_dq,output wire dram_clk
);
    (* async_reg="true" *) reg [2:0] sys_reset=7,mem_reset=7;
    always @(posedge clk_sys or posedge reset_async)
        if(reset_async) sys_reset<=7;else sys_reset<={sys_reset[1:0],1'b0};
    always @(posedge clk_mem or posedge reset_async)
        if(reset_async) mem_reset<=7;else mem_reset<={mem_reset[1:0],1'b0};
    assign client_reset=sys_reset[2];
    reg [1:0] round_robin=0,locked_owner=0;
    reg locked=0;
    reg selected_valid;
    reg [1:0] selected;
    wire accepted,controller_ready,return_valid,return_next,phy_idle,core_initialized;
    wire [2:0] return_tag;
    reg [3:0] outstanding_reads=0;
    always @* begin
        selected=locked_owner;selected_valid=locked && valid[locked_owner];
        if(!locked) begin
            selected=round_robin;selected_valid=0;
            for(integer i=0;i<4;i++) begin
                if(!selected_valid && valid[2'(round_robin+i)]) begin
                    selected=2'(round_robin+i);selected_valid=1;
                end
            end
        end
    end
    // Keep the readiness return separate from arbitration selection. It must
    // not retrigger owner selection through a combinational sensitivity loop.
    assign ready=initialized && selected_valid ?
        ((4'b0001<<selected) & {4{controller_ready}}):4'b0;
    assign accepted=selected_valid && controller_ready && initialized;
    assign initialized=core_initialized && !sys_reset[2];
    assign rvalid=return_valid && !sys_reset[2] ? (4'b0001<<return_tag[1:0]):4'b0;
    // This is backend/pin idle, not source-mailbox or response-FIFO drain.
    assign backend_idle=initialized && !selected_valid && outstanding_reads==0 &&
        !return_valid && !return_next && phy_idle;
    always @(posedge clk_sys) begin
        if(sys_reset[2]) begin
            round_robin<=0;locked_owner<=0;locked<=0;outstanding_reads<=0;fault<=0;
        end else begin
            if(selected_valid && !accepted) begin locked<=1;locked_owner<=selected;end
            if(accepted) begin locked<=0;round_robin<=selected+1'b1;end
            if(locked && !valid[locked_owner]) fault<=1;
            case({accepted && !write[selected],return_valid})
                2'b10:outstanding_reads<=outstanding_reads+1'b1;
                2'b01:outstanding_reads<=outstanding_reads-1'b1;
                default:;
            endcase
            if(return_valid && (return_tag[2] || outstanding_reads==0)) fault<=1;
            if(accepted && !write[selected] && outstanding_reads==15 && !return_valid) fault<=1;
        end
    end
    wire [1:0] ras,cas,wen;
    wire [25:0] address;
    wire [3:0] bank,mask;
    wire cke,wren,rden,phy_valid;
    wire [31:0] wdata,phy_data;
    sdr_ctrl #(.T_INIT(T_INIT),.T_REFI(T_REFI),.RL(5),.AHEAD(0)) controller (
        .clk(clk_sys),.rst(sys_reset[2]),.ready(core_initialized),
        .req_valid(selected_valid && !sys_reset[2]),.req_ready(controller_ready),
        .req_we(write[selected]),.req_addr(addr[selected]),.req_wdata(data[selected]),
        .req_be(byte_en[selected]),.req_tag({1'b0,selected}),.la_valid(1'b0),.la_addr(24'b0),
        .rd_valid(return_valid),.rd_data(rdata),.rd_tag(return_tag),.rd_next(return_next),.rd_next_tag(),
        .dfi_ras_n(ras),.dfi_cas_n(cas),.dfi_we_n(wen),.dfi_addr(address),.dfi_ba(bank),.dfi_cke(cke),
        .dfi_wrdata_en(wren),.dfi_wrdata(wdata),.dfi_wrdata_mask(mask),.dfi_rddata_en(rden),
        .dfi_rddata(phy_data),.dfi_rddata_valid(phy_valid));
    psx_sdr_phy_status phy (
        .clk(clk_sys),.rst(sys_reset[2]),.clk2x(clk_mem),.rst2x(mem_reset[2]),.clk2x_90(clk_dram),
        .ras_n(ras),.cas_n(cas),.we_n(wen),.addr(address),.ba(bank),.cke(cke),
        .wrdata_en(wren),.wrdata(wdata),.wrdata_mask(mask),.rddata_en(rden),
        .rddata(phy_data),.rddata_valid(phy_valid),.traffic_idle(phy_idle),
        .sdram_a(dram_a),.sdram_ba(dram_ba),.sdram_dm(dram_dqm),.sdram_dq(dram_dq),
        .sdram_ras_n(dram_ras_n),.sdram_cas_n(dram_cas_n),.sdram_we_n(dram_we_n),
        .sdram_cke(dram_cke),.sdram_clock(dram_clk));
endmodule
