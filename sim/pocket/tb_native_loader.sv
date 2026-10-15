// psx_native_memory's BIOS-loader path against a stand-in sdram (ch3 only):
// a 1-word packet and a 4-word packet (psx_bios_patch's shell patch) must
// become one ch3 write per word at 0x800000 + 2*halfword address, in order,
// with each word's byte enables, and one response per packet.
`timescale 1ns/1ps
module sdram #(parameter SINGLE_CHIP=0) (
    input init, clk, clk_base, SDRAM_EN, inout [15:0] SDRAM_DQ, output [12:0] SDRAM_A, output SDRAM_DQML, SDRAM_DQMH,
    output [1:0] SDRAM_BA, output SDRAM_nCS, SDRAM_nWE, SDRAM_nRAS, SDRAM_nCAS, SDRAM_CKE, SDRAM_CLK,
    input refreshForce, output ram_idle,
    input [26:0] ch1_addr, output [127:0] ch1_dout, output [31:0] ch1_dout32, input [15:0] ch1_din, input ch1_req, ch1_rnw, ch1_dma,
    input [1:0] ch1_cntDMA, input ch1_cache, output ch1_ready, output [3:0] cache_wr, output [31:0] cache_data, output [7:0] cache_addr,
    output dma_wr, dma_reqprocessed, output [31:0] dma_data,
    input [26:0] ch2_addr, output [31:0] ch2_dout, input [31:0] ch2_din, input ch2_req, ch2_rnw, input [3:0] ch2_be, output ch2_ready,
    input [26:0] ch3_addr, output [31:0] ch3_dout, input [31:0] ch3_din, input ch3_req, ch3_rnw, input [3:0] ch3_be, output reg ch3_ready=0,
    input [26:0] dmafifo_adr, input [31:0] dmafifo_data, input dmafifo_empty, output dmafifo_read);
    integer n=0, wait_count=-1;
    reg [26:0] a[0:7]; reg [31:0] d[0:7]; reg [3:0] b[0:7];
    always @(posedge clk_base) begin
        ch3_ready<=0;
        if (ch3_req) begin a[n]=ch3_addr; d[n]=ch3_din; b[n]=ch3_be; n=n+1; wait_count=3; end
        else if (wait_count>0) wait_count=wait_count-1;
        else if (wait_count==0) begin ch3_ready<=1; wait_count=-1; end
    end
endmodule

module tb_native_loader;
    reg clk1x=0, clk3x=0; always #15 clk1x=~clk1x; always #5 clk3x=~clk3x;
    reg valid=0, rsp_ready=1; reg [24:0] addr=0; reg [3:0] hw=0; reg [127:0] data=0; reg [15:0] be=0;
    wire ready, rsp; integer errs=0, rsps=0;
    psx_native_memory dut(.clk1x(clk1x),.clk3x(clk3x),.init(1'b0),.cpu_reset(1'b1),.ram_refresh(1'b0),.ram_ena(1'b0),.ram_rnw(1'b1),
        .ram_dma(1'b0),.ram_cache(1'b0),.ram_adr(25'b0),.ram_cntDMA(2'b0),.ram_dataWrite(32'b0),.ram_be(4'b0),.ram_done(),.ram_dataRead32(),
        .dma_wr(),.dma_data(),.dma_reqprocessed(),.cache_wr(),.cache_data(),.cache_addr(),.ram_dmafifo_adr(23'b0),.ram_dmafifo_data(32'b0),
        .ram_dmafifo_empty(1'b1),.ram_dmafifo_read(),.spuram_ena(1'b0),.spuram_rnw(1'b1),.spuram_adr(19'b0),.spuram_dataWrite(32'b0),
        .spuram_be(4'b0),.spuram_done(),.spuram_dataRead(),.load_valid(valid),.load_addr(addr),.load_halfwords(hw),.load_data(data),
        .load_be(be),.load_ready(ready),.load_rsp(rsp),.load_rsp_ready(rsp_ready),.dram_dq(),.dram_a(),.dram_dqm(),.dram_ba(),
        .dram_we_n(),.dram_ras_n(),.dram_cas_n(),.dram_cke(),.dram_clk());
    always @(posedge clk1x) if (rsp && rsp_ready) rsps=rsps+1;
    task send(input [24:0] ad, input [3:0] h, input [127:0] dt, input [15:0] bb);
        begin
            @(negedge clk1x); valid=1; addr=ad; hw=h; data=dt; be=bb;
            @(posedge clk1x); while (!ready) @(posedge clk1x);
            @(negedge clk1x); valid=0;
            while (!rsp) @(posedge clk1x);
            @(posedge clk1x);
        end
    endtask
    initial begin
        repeat (4) @(posedge clk1x);
        send(25'h00010, 4'd2, {96'b0, 32'h11223344}, 16'h000f);
        send(25'h06000, 4'd8, 128'hdddddddd_cccccccc_bbbbbbbb_aaaaaaaa, 16'hf3ff);
        repeat (4) @(posedge clk1x);
        if (dut.controller.n!==5 || rsps!==2) begin errs=errs+1; $display("writes %0d responses %0d", dut.controller.n, rsps); end
        if (dut.controller.a[0]!==27'h800020 || dut.controller.d[0]!==32'h11223344 || dut.controller.b[0]!==4'hf) begin errs=errs+1; $display("w0 %h %h", dut.controller.a[0], dut.controller.d[0]); end
        if (dut.controller.a[1]!==27'h80c000 || dut.controller.d[1]!==32'haaaaaaaa || dut.controller.b[1]!==4'hf) begin errs=errs+1; $display("w1 %h", dut.controller.a[1]); end
        if (dut.controller.a[2]!==27'h80c004 || dut.controller.d[2]!==32'hbbbbbbbb || dut.controller.b[2]!==4'hf) begin errs=errs+1; $display("w2 %h", dut.controller.a[2]); end
        if (dut.controller.a[3]!==27'h80c008 || dut.controller.d[3]!==32'hcccccccc || dut.controller.b[3]!==4'h3) begin errs=errs+1; $display("w3 %h %h", dut.controller.a[3], dut.controller.b[3]); end
        if (dut.controller.a[4]!==27'h80c00c || dut.controller.d[4]!==32'hdddddddd || dut.controller.b[4]!==4'hf) begin errs=errs+1; $display("w4 %h", dut.controller.a[4]); end
        $display("errs=%0d", errs); $finish;
    end
endmodule
