// SPDX-License-Identifier: MIT
// Observe the real controller's MRS and forwarded clock. This checks only
// the programmed CAS/rate and pin-clock polarity, never data-beat correctness.
`timescale 1ns/1fs
`default_nettype none
module tb_native_sdram_spec #(parameter integer ACTIVE_CAS = 2);
    reg clk = 0, clk_base = 0, init = 1;
    real half_period = 4.9205;
    initial begin
        if ($value$plusargs("half_period=%f", half_period)) begin end
        forever #(half_period) clk = ~clk;
    end
    initial forever #(3*half_period) clk_base = ~clk_base;
    initial begin repeat (6) @(negedge clk); init = 0; end

    wire [15:0] dq;
    wire [12:0] a;
    wire [1:0] ba;
    wire ncs, nwe, nras, ncas, chip_clk;
    psx_sdram_ctrl #(.SINGLE_CHIP(1), .POWERUP_CYCLES(32)
`ifdef NATIVE_SPEC_PARAMETERIZED_CAS
        , .CAS_LATENCY(ACTIVE_CAS)
`endif
    ) dut (
        .init(init), .clk(clk), .clk_base(clk_base), .SDRAM_EN(1'b1),
        .SDRAM_DQ(dq), .SDRAM_A(a), .SDRAM_BA(ba), .SDRAM_nCS(ncs),
        .SDRAM_nWE(nwe), .SDRAM_nRAS(nras), .SDRAM_nCAS(ncas),
        .SDRAM_CLK(chip_clk), .refreshForce(1'b0),
        .ch1_addr(27'b0), .ch1_din(16'b0), .ch1_req(1'b0),
        .ch1_rnw(1'b1), .ch1_dma(1'b0), .ch1_cntDMA(2'b0), .ch1_cache(1'b0),
        .ch2_addr(27'b0), .ch2_din(32'b0), .ch2_req(1'b0), .ch2_rnw(1'b1), .ch2_be(4'b0),
        .ch3_addr(27'b0), .ch3_din(32'b0), .ch3_req(1'b0), .ch3_rnw(1'b1), .ch3_be(4'b0),
        .dmafifo_adr(27'b0), .dmafifo_data(32'b0), .dmafifo_empty(1'b1)
    );
    real previous_edge = 0, period_ns = 0, minimum_ns;
    integer edges = 0, cas_latency, mrs_count = 0, conformant;
    always @(posedge chip_clk) begin
        if (edges > 0) period_ns = $realtime - previous_edge;
        previous_edge = $realtime;
        edges = edges + 1;
        if (!init && !ncs && {nras,ncas,nwe} == 3'b000) begin
            mrs_count = mrs_count + 1;
            if (edges < 10 || mrs_count != 1 || ba !== 2'b00 ||
                (a !== 13'h0221 && a !== 13'h0231)) $fatal(1,"SPEC_GEOMETRY mode/clock");
            cas_latency = a[6:4];
            minimum_ns = cas_latency == 2 ? 12.0 : 6.0;
            // Floating-point comparison tolerance is 1fs, not extra timing
            // margin. The 1ps-below-limit controls must still fail.
            conformant = period_ns + 0.000001 >= minimum_ns;
            $display("NATIVE_SPEC MRS=%04h CAS=%0d PERIOD_NS=%0.3f MIN_NS=%0.3f CONFORMANT=%0d",
                     a,cas_latency,period_ns,minimum_ns,conformant);
            // Allow a delta for the portable DDIO model, then check the
            // expected inverted clock relationship at this physical edge.
            #0.001;
            if (clk !== 1'b0) $fatal(1,"SPEC_POLARITY forwarded clock is not inverted");
            $display("NATIVE_SPEC_COMPLETE");
            $finish;
        end
    end
    initial begin #100000; $fatal(1,"SPEC_TIMEOUT missing MRS"); end
endmodule
`default_nettype wire
