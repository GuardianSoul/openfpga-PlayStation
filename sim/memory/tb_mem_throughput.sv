`timescale 1ns/1ps
`default_nettype none

// Throughput and latency of the normalized memory frontends for PSX-like
// access patterns, at the 62.5 MHz client clock. BACKEND 0: psx_sdram on the
// cycle-checked DFI command model. BACKEND 1: psx_psram on the timing-checked
// CellularRAM models. One request is outstanding at a time, as both
// frontends allow; the response is accepted immediately.
module tb_mem_throughput;
    parameter integer BACKEND = 0;
    localparam integer REQUESTS = 400;
    reg clk = 0;
    always #8 clk = ~clk;
    reg rst = 1;
    reg req_valid = 0, req_write = 0;
    reg [24:0] req_addr = 0;
    reg [3:0] req_halfwords = 0;
    reg [127:0] req_wdata = 0;
    reg [15:0] req_byte_en = 16'hFFFF;
    wire req_ready, rsp_valid, rsp_error, initialized;
    wire [127:0] rsp_rdata;

    generate if (BACKEND == 0) begin : sdram
        wire [1:0] ras_n, cas_n, we_n;
        wire [25:0] dfi_addr;
        wire [3:0] dfi_ba, mask;
        wire cke, wren, rden, rvalid;
        wire [31:0] wdata, rdata;
        psx_sdram memory (
            .clk(clk), .rst(rst), .initialized(initialized),
            .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write), .req_addr(req_addr),
            .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
            .rsp_valid(rsp_valid), .rsp_ready(1'b1), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
            .dfi_ras_n(ras_n), .dfi_cas_n(cas_n), .dfi_we_n(we_n), .dfi_addr(dfi_addr), .dfi_ba(dfi_ba),
            .dfi_cke(cke), .dfi_wrdata_en(wren), .dfi_wrdata(wdata), .dfi_wrdata_mask(mask),
            .dfi_rddata_en(rden), .dfi_rddata(rdata), .dfi_rddata_valid(rvalid)
        );
        sdr_command_model model (
            .clk(clk), .rst(rst), .ras_n(ras_n), .cas_n(cas_n), .we_n(we_n), .address(dfi_addr),
            .bank(dfi_ba), .cke(cke), .wrdata_en(wren), .rddata_en(rden), .wrdata(wdata),
            .wrdata_mask(mask), .rddata(rdata), .rddata_valid(rvalid)
        );
    end else begin : psram
        wire [21:16] a0, a1;
        wire [15:0] dq0, dq1;
        wire clk0, clk1, adv0, adv1, cre0, cre1, ce00, ce01, ce10, ce11, oe0, oe1, we0, we1;
        wire ub0, lb0, ub1, lb1;
        psx_psram #(.T_INIT(20)) memory (
            .clk(clk), .rst(rst), .initialized(initialized),
            .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write), .req_addr(req_addr),
            .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
            .rsp_valid(rsp_valid), .rsp_ready(1'b1), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
            .cram0_a(a0), .cram1_a(a1), .cram0_dq(dq0), .cram1_dq(dq1),
            .cram0_wait(1'b0), .cram1_wait(1'b0), .cram0_clk(clk0), .cram1_clk(clk1),
            .cram0_adv_n(adv0), .cram1_adv_n(adv1), .cram0_cre(cre0), .cram1_cre(cre1),
            .cram0_ce0_n(ce00), .cram0_ce1_n(ce01), .cram1_ce0_n(ce10), .cram1_ce1_n(ce11),
            .cram0_oe_n(oe0), .cram1_oe_n(oe1), .cram0_we_n(we0), .cram1_we_n(we1),
            .cram0_ub_n(ub0), .cram0_lb_n(lb0), .cram1_ub_n(ub1), .cram1_lb_n(lb1)
        );
        cellularram_async_model #(.CHIP(0)) chip0 (
            .a(a0), .dq(dq0), .host_oe(memory.memory.chip_lo.data_out_en), .ce0_n(ce00), .ce1_n(ce01),
            .adv_n(adv0), .oe_n(oe0), .we_n(we0), .ub_n(ub0), .lb_n(lb0), .cre(cre0)
        );
        cellularram_async_model #(.CHIP(1)) chip1 (
            .a(a1), .dq(dq1), .host_oe(memory.memory.chip_hi.data_out_en), .ce0_n(ce10), .ce1_n(ce11),
            .adv_n(adv1), .oe_n(oe1), .we_n(we1), .ub_n(ub1), .lb_n(lb1), .cre(cre1)
        );
    end endgenerate

    reg [31:0] random_state = 32'h4D454D31;
    function automatic [31:0] next_random(input [31:0] state);
        next_random = state * 32'd1664525 + 32'd1013904223;
    endfunction

    // Pattern: halfwords per request, write or read, sequential or random
    // halfword addresses inside the first 8 KiB (both models store it).
    task automatic measure(input [8*24-1:0] name, input integer halfwords, input reg write,
                           input reg sequential);
        integer start, latency_sum, latency_max, issued_at, cycle;
        reg [24:0] address;
        begin
            address = 0; latency_sum = 0; latency_max = 0; cycle = 0;
            @(negedge clk);
            start = $time;
            for (integer n = 0; n < REQUESTS; n = n + 1) begin
                if (sequential) address = 25'((n * halfwords) % 4096);
                else begin
                    random_state = next_random(random_state);
                    address = 25'((random_state[27:16] % (4096 / halfwords)) * halfwords);
                end
                while (!req_ready) @(negedge clk);
                req_valid = 1; req_write = write; req_addr = address; req_halfwords = 4'(halfwords);
                req_wdata = {4{random_state}};
                issued_at = $time;
                @(negedge clk); req_valid = 0;
                while (!rsp_valid) @(negedge clk);
                if (rsp_error) $fatal(1, "Unexpected error response");
                cycle = ($time - issued_at) / 16;
                latency_sum = latency_sum + cycle;
                if (cycle > latency_max) latency_max = cycle;
                @(negedge clk);
            end
            $display("%-24s %3d B  %6.1f MB/s  latency mean %5.1f max %3d cycles",
                     name, halfwords * 2, real'(REQUESTS * halfwords * 2) * 1000.0 / real'($time - start),
                     real'(latency_sum) / REQUESTS, latency_max);
        end
    endtask

    initial begin
        repeat (3) @(negedge clk);
        rst = 0;
        while (!initialized) @(negedge clk);
        $display("Backend %s at 62.5 MHz, one request outstanding:", BACKEND == 0 ? "SDRAM" : "PSRAM");
        measure("sequential read", 8, 0, 1);
        measure("sequential write", 4, 1, 1);
        measure("sequential write", 8, 1, 1);
        measure("random read", 4, 0, 0);
        measure("random read", 8, 0, 0);
        measure("random write", 2, 1, 0);
        $finish;
    end
endmodule
`default_nettype wire
