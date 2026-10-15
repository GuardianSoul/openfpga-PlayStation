`timescale 1ns/1ps
`default_nettype none

// PSX transactions through psx_psram and agg23's controllers to two timing-
// checked CellularRAM models, against the byte oracle in psram_vectors.py.
module tb_psx_psram;
    parameter real CLOCK_MHZ = 62.5;         // what the controllers are told
    parameter real CHIP_T_AW = 70.0;         // the models' write cycle time
    reg clk = 0;
    always #8 clk = ~clk;                    // 62.5 MHz
    reg rst = 1;
    reg req_valid = 0, req_write = 0, rsp_ready = 0;
    reg [24:0] req_addr = 0;
    reg [3:0] req_halfwords = 0;
    reg [127:0] req_wdata = 0;
    reg [15:0] req_byte_en = 0;
    wire req_ready, rsp_valid, rsp_error, initialized;
    wire [127:0] rsp_rdata;
    wire [21:16] cram0_a, cram1_a;
    wire [15:0] cram0_dq, cram1_dq;
    wire cram0_clk, cram1_clk, cram0_adv_n, cram1_adv_n, cram0_cre, cram1_cre;
    wire cram0_ce0_n, cram0_ce1_n, cram1_ce0_n, cram1_ce1_n;
    wire cram0_oe_n, cram1_oe_n, cram0_we_n, cram1_we_n;
    wire cram0_ub_n, cram0_lb_n, cram1_ub_n, cram1_lb_n;

    psx_psram #(.CLOCK_MHZ(CLOCK_MHZ), .T_INIT(20)) dut (
        .clk(clk), .rst(rst), .initialized(initialized),
        .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write), .req_addr(req_addr),
        .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .cram0_a(cram0_a), .cram1_a(cram1_a), .cram0_dq(cram0_dq), .cram1_dq(cram1_dq),
        .cram0_wait(1'b0), .cram1_wait(1'b0), .cram0_clk(cram0_clk), .cram1_clk(cram1_clk),
        .cram0_adv_n(cram0_adv_n), .cram1_adv_n(cram1_adv_n), .cram0_cre(cram0_cre), .cram1_cre(cram1_cre),
        .cram0_ce0_n(cram0_ce0_n), .cram0_ce1_n(cram0_ce1_n), .cram1_ce0_n(cram1_ce0_n), .cram1_ce1_n(cram1_ce1_n),
        .cram0_oe_n(cram0_oe_n), .cram1_oe_n(cram1_oe_n), .cram0_we_n(cram0_we_n), .cram1_we_n(cram1_we_n),
        .cram0_ub_n(cram0_ub_n), .cram0_lb_n(cram0_lb_n), .cram1_ub_n(cram1_ub_n), .cram1_lb_n(cram1_lb_n)
    );
    cellularram_async_model #(.CHIP(0), .T_AW(CHIP_T_AW)) chip0 (
        .a(cram0_a), .dq(cram0_dq), .host_oe(dut.memory.chip_lo.data_out_en),
        .ce0_n(cram0_ce0_n), .ce1_n(cram0_ce1_n), .adv_n(cram0_adv_n), .oe_n(cram0_oe_n),
        .we_n(cram0_we_n), .ub_n(cram0_ub_n), .lb_n(cram0_lb_n), .cre(cram0_cre)
    );
    cellularram_async_model #(.CHIP(1), .T_AW(CHIP_T_AW)) chip1 (
        .a(cram1_a), .dq(cram1_dq), .host_oe(dut.memory.chip_hi.data_out_en),
        .ce0_n(cram1_ce0_n), .ce1_n(cram1_ce1_n), .adv_n(cram1_adv_n), .oe_n(cram1_oe_n),
        .we_n(cram1_we_n), .ub_n(cram1_ub_n), .lb_n(cram1_lb_n), .cre(cram1_cre)
    );

    reg [31:0] random_state = 32'h50535250;
    always @(posedge clk) random_state <= random_state * 32'd1664525 + 32'd1013904223;

    // Pins that must stay idle in asynchronous mode, and responses that must
    // hold under backpressure.
    reg stalled = 0;
    reg [128:0] stalled_response;
    always @(posedge clk) begin
        if (cram0_clk || cram1_clk || cram0_cre || cram1_cre) $fatal(1, "PSRAM clock or CRE driven");
        if (!rst && stalled && (!rsp_valid || {rsp_error, rsp_rdata} !== stalled_response))
            $fatal(1, "Response changed under backpressure");
        stalled <= !rst && rsp_valid && !rsp_ready;
        stalled_response <= {rsp_error, rsp_rdata};
        if (!rst && !initialized && (dut.memory.mem_valid && dut.memory.mem_ready))
            $fatal(1, "Access before initialization");
    end

    integer file, fields, testcase = 0, waited, total_cycles = 0, words = 0;
    reg [8191:0] vector_path;
    reg [24:0] address;
    reg [3:0] count;
    reg write_flag, expected_error;
    reg [127:0] data, expected_data;
    reg [15:0] mask;

    // Reset in the middle of a read: the chips' controllers finish it, its
    // completion must not surface, and the next request must wait for them.
    task automatic abort_read;
        begin
            while (!req_ready) @(negedge clk);
            req_valid = 1; req_write = 0; req_addr = 25'h123; req_halfwords = 8;
            @(negedge clk); req_valid = 0;
            repeat (4) @(negedge clk);
            if (!dut.memory.pending) $fatal(1, "Abort test did not start a read");
            rst = 1;
            @(negedge clk);
            if (req_ready || rsp_valid) $fatal(1, "Reset did not gate interfaces");
            rst = 0;
            waited = 0;
            while (!req_ready) begin
                @(negedge clk);
                if (rsp_valid) $fatal(1, "Stale completion after reset");
                waited = waited + 1;
                if (waited > 100) $fatal(1, "No recovery after reset");
            end
        end
    endtask

    initial begin
        if (!$value$plusargs("vectors=%s", vector_path)) $fatal(1, "Missing vector file");
        file = $fopen(vector_path, "r");
        if (!file) $fatal(1, "Cannot open vectors");
        repeat (3) @(negedge clk);
        rst = 0;
        abort_read();
        while (!$feof(file)) begin
            fields = $fscanf(file, "%h %h %h %h %h %h %h\n",
                             address, count, write_flag, data, mask, expected_error, expected_data);
            if (fields != 7) $fatal(1, "Malformed vector row");
            testcase = testcase + 1;
            @(negedge clk);
            if (!req_ready) $fatal(1, "Frontend did not return idle in case %0d", testcase);
            req_valid = 1; req_addr = address; req_halfwords = count;
            req_write = write_flag; req_wdata = data; req_byte_en = mask;
            @(negedge clk); req_valid = 0;
            req_addr = ~address; req_wdata = ~data; req_byte_en = ~mask; req_halfwords = 0;
            waited = 1;
            rsp_ready = random_state[5:4] != 0;
            while (!(rsp_valid && rsp_ready)) begin
                @(negedge clk); waited = waited + 1;
                rsp_ready = random_state[5:4] != 0;
                if (waited > 400) $fatal(1, "Transaction timeout in case %0d", testcase);
            end
            if (rsp_error !== expected_error || rsp_rdata !== expected_data)
                $fatal(1, "Response mismatch case %0d addr=%h count=%0d write=%0d got=%h expected=%h error=%b expected=%b",
                       testcase, address, count, write_flag, rsp_rdata, expected_data, rsp_error, expected_error);
            if (!expected_error) begin
                total_cycles = total_cycles + waited;
                words = words + (32'(count) + 32'(address[0]) + 1) / 2;
            end
            @(negedge clk); rsp_ready = 0;
        end
        $display("PSRAM: %0d transactions, %0d reads / %0d writes per chip, %0d words in %0d cycles",
                 testcase, chip0.reads, chip0.writes, words, total_cycles);
        if (chip0.reads != chip1.reads || chip0.writes != chip1.writes) $fatal(1, "Chips diverged");
        $finish;
    end
endmodule
`default_nettype wire
