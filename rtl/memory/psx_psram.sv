`timescale 1ns/1ps
`default_nettype none

// The Pocket's two CellularRAM chips (cram0/cram1, AS1C8M16PL-70) as one
// 32-bit word memory: cram0 holds each word's low halfword and cram1 its high
// halfword, both driven alike. A word address selects die (bit 22, CE0#/CE1#)
// and the 4 M halfword address inside it, so the space is 8 M words (32 MiB).
//
// Each chip uses Adam Gastineau's asynchronous controller from the pinned
// analogue-pocket-utils reference, unmodified: it is the one Pocket PSRAM
// controller in our references that has run on hardware. At 62.5 MHz an
// access takes seven cycles (about 36 MB/s for 32-bit words). The chips are
// left in their asynchronous power-up mode; no configuration register is
// written. Synchronous bursts (MIRLO64's psram.sv) remain a later option.
module psx_psram_word #(
    parameter real CLOCK_MHZ = 62.5,
    parameter integer T_INIT = 9375          // 150 us power-up wait at 62.5 MHz
) (
    input wire clk, rst,
    output wire initialized,
    input wire mem_valid,
    output wire mem_ready,
    input wire mem_write,
    input wire [23:0] mem_addr,
    input wire [31:0] mem_wdata,
    input wire [3:0] mem_byte_en,
    output wire mem_rvalid,
    output wire [31:0] mem_rdata,
    output wire [21:16] cram0_a, cram1_a,
    inout wire [15:0] cram0_dq, cram1_dq,
    input wire cram0_wait, cram1_wait,
    output wire cram0_clk, cram1_clk,
    output wire cram0_adv_n, cram1_adv_n,
    output wire cram0_cre, cram1_cre,
    output wire cram0_ce0_n, cram0_ce1_n, cram1_ce0_n, cram1_ce1_n,
    output wire cram0_oe_n, cram1_oe_n,
    output wire cram0_we_n, cram1_we_n,
    output wire cram0_ub_n, cram0_lb_n, cram1_ub_n, cram1_lb_n
);
    reg [$clog2(T_INIT + 1)-1:0] init_count;
    reg init_done, pending;
    wire busy_lo, busy_hi, avail_lo, avail_hi;
    wire [15:0] data_lo, data_hi;
    assign initialized = init_done && !rst;
    // The controllers have no reset: an access started before a reset still
    // finishes, and its completion is ignored unless a read is pending.
    assign mem_ready = initialized && !pending && !busy_lo && !busy_hi;
    wire start = mem_valid && mem_ready;
    wire read_en = start && !mem_write;
    wire write_en = start && mem_write;
    assign mem_rvalid = pending && avail_lo;
    assign mem_rdata = {data_hi, data_lo};

    always @(posedge clk) begin
        if (rst) begin
            init_count <= 0; init_done <= 0; pending <= 0;
        end else begin
            if (!init_done) begin
                init_count <= init_count + 1'd1;
                if (init_count == T_INIT[$bits(init_count)-1:0]) init_done <= 1;
            end
            if (read_en) pending <= 1;
            else if (mem_rvalid) pending <= 0;
        end
    end

    psram #(.CLOCK_SPEED(CLOCK_MHZ)) chip_lo (
        .clk(clk), .bank_sel(mem_addr[22]), .addr(mem_addr[21:0]),
        .write_en(write_en), .data_in(mem_wdata[15:0]),
        .write_high_byte(mem_byte_en[1]), .write_low_byte(mem_byte_en[0]),
        .read_en(read_en), .read_avail(avail_lo), .data_out(data_lo), .busy(busy_lo),
        .cram_a(cram0_a), .cram_dq(cram0_dq), .cram_wait(cram0_wait), .cram_clk(cram0_clk),
        .cram_adv_n(cram0_adv_n), .cram_cre(cram0_cre), .cram_ce0_n(cram0_ce0_n), .cram_ce1_n(cram0_ce1_n),
        .cram_oe_n(cram0_oe_n), .cram_we_n(cram0_we_n), .cram_ub_n(cram0_ub_n), .cram_lb_n(cram0_lb_n)
    );
    psram #(.CLOCK_SPEED(CLOCK_MHZ)) chip_hi (
        .clk(clk), .bank_sel(mem_addr[22]), .addr(mem_addr[21:0]),
        .write_en(write_en), .data_in(mem_wdata[31:16]),
        .write_high_byte(mem_byte_en[3]), .write_low_byte(mem_byte_en[2]),
        .read_en(read_en), .read_avail(avail_hi), .data_out(data_hi), .busy(busy_hi),
        .cram_a(cram1_a), .cram_dq(cram1_dq), .cram_wait(cram1_wait), .cram_clk(cram1_clk),
        .cram_adv_n(cram1_adv_n), .cram_cre(cram1_cre), .cram_ce0_n(cram1_ce0_n), .cram_ce1_n(cram1_ce1_n),
        .cram_oe_n(cram1_oe_n), .cram_we_n(cram1_we_n), .cram_ub_n(cram1_ub_n), .cram_lb_n(cram1_lb_n)
    );
`ifndef SYNTHESIS
    always @(posedge clk) if (avail_lo !== avail_hi || busy_lo !== busy_hi)
        if (init_done) $fatal(1, "PSRAM chip controllers lost lockstep");
`endif
endmodule

// Normalized PSX transactions (as psx_sdram) onto the PSRAM word memory.
// Halfword addresses at or past 16 M (32 MiB) complete with rsp_error and
// touch no memory.
module psx_psram #(
    parameter real CLOCK_MHZ = 62.5,
    parameter integer T_INIT = 9375
) (
    input wire clk, rst,
    output wire initialized,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [24:0] req_addr,
    input wire [3:0] req_halfwords,
    input wire [127:0] req_wdata,
    input wire [15:0] req_byte_en,
    output wire rsp_valid,
    input wire rsp_ready,
    output wire [127:0] rsp_rdata,
    output wire rsp_error,
    output wire [21:16] cram0_a, cram1_a,
    inout wire [15:0] cram0_dq, cram1_dq,
    input wire cram0_wait, cram1_wait,
    output wire cram0_clk, cram1_clk,
    output wire cram0_adv_n, cram1_adv_n,
    output wire cram0_cre, cram1_cre,
    output wire cram0_ce0_n, cram0_ce1_n, cram1_ce0_n, cram1_ce1_n,
    output wire cram0_oe_n, cram1_oe_n,
    output wire cram0_we_n, cram1_we_n,
    output wire cram0_ub_n, cram0_lb_n, cram1_ub_n, cram1_lb_n
);
    wire word_ready, bridge_ready, bridge_rsp_valid, bridge_rsp_error;
    wire mem_valid, mem_ready, mem_write, mem_rvalid;
    wire [23:0] mem_addr;
    wire [31:0] mem_wdata, mem_rdata;
    wire [3:0] mem_byte_en;
    wire [127:0] bridge_rdata;
    wire [25:0] last_halfword = {1'b0, req_addr} + {22'b0, req_halfwords} - 26'd1;
    wire out_of_range = req_halfwords != 0 && last_halfword[25:24] != 0;
    reg range_error;
    // An out-of-range request is answered here, never reaching the bridge.
    assign req_ready = initialized && !range_error && bridge_ready;
    assign rsp_valid = range_error || bridge_rsp_valid;
    assign rsp_error = range_error || bridge_rsp_error;
    assign rsp_rdata = range_error ? 128'b0 : bridge_rdata;
    always @(posedge clk)
        if (rst) range_error <= 0;
        else if (req_valid && req_ready && out_of_range) range_error <= 1;
        else if (range_error && rsp_ready) range_error <= 0;

    psx_mem_bridge bridge (
        .clk(clk), .rst(rst), .req_valid(req_valid && req_ready && !out_of_range), .req_ready(bridge_ready),
        .req_write(req_write), .req_addr(req_addr), .req_halfwords(req_halfwords),
        .req_wdata(req_wdata), .req_byte_en(req_byte_en),
        .rsp_valid(bridge_rsp_valid), .rsp_ready(rsp_ready && !range_error), .rsp_rdata(bridge_rdata),
        .rsp_error(bridge_rsp_error),
        .mem_valid(mem_valid), .mem_ready(mem_ready), .mem_write(mem_write), .mem_addr(mem_addr),
        .mem_wdata(mem_wdata), .mem_byte_en(mem_byte_en), .mem_rvalid(mem_rvalid), .mem_rdata(mem_rdata)
    );
    psx_psram_word #(.CLOCK_MHZ(CLOCK_MHZ), .T_INIT(T_INIT)) memory (
        .clk(clk), .rst(rst), .initialized(initialized),
        .mem_valid(mem_valid), .mem_ready(mem_ready), .mem_write(mem_write), .mem_addr(mem_addr),
        .mem_wdata(mem_wdata), .mem_byte_en(mem_byte_en), .mem_rvalid(mem_rvalid), .mem_rdata(mem_rdata),
        .cram0_a(cram0_a), .cram1_a(cram1_a), .cram0_dq(cram0_dq), .cram1_dq(cram1_dq),
        .cram0_wait(cram0_wait), .cram1_wait(cram1_wait), .cram0_clk(cram0_clk), .cram1_clk(cram1_clk),
        .cram0_adv_n(cram0_adv_n), .cram1_adv_n(cram1_adv_n), .cram0_cre(cram0_cre), .cram1_cre(cram1_cre),
        .cram0_ce0_n(cram0_ce0_n), .cram0_ce1_n(cram0_ce1_n), .cram1_ce0_n(cram1_ce0_n), .cram1_ce1_n(cram1_ce1_n),
        .cram0_oe_n(cram0_oe_n), .cram1_oe_n(cram1_oe_n), .cram0_we_n(cram0_we_n), .cram1_we_n(cram1_we_n),
        .cram0_ub_n(cram0_ub_n), .cram0_lb_n(cram0_lb_n), .cram1_ub_n(cram1_ub_n), .cram1_lb_n(cram1_lb_n)
    );
endmodule
`default_nettype wire
