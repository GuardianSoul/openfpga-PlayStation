`timescale 1ns/1ps
`default_nettype none

module tb_psx_mem_bridge;
    parameter integer CONTROLLER = 0;
    parameter real PIN_T_AC = 2.5;
    parameter real PIN_T_AC_SECOND = PIN_T_AC;
    parameter real PIN_T_HZ = 2.5;
    reg clk = 0;
    always #8 clk = ~clk;
    reg clk_mem = 1;
    always #4 clk_mem = ~clk_mem;
    wire clk_dram = ~clk_mem;
    reg rst = 1;
    reg req_valid = 0, req_write = 0, rsp_ready = 0;
    reg [24:0] req_addr = 0;
    reg [3:0] req_halfwords = 0;
    reg [127:0] req_wdata = 0;
    reg [15:0] req_byte_en = 0;
    wire req_ready, rsp_valid, rsp_error;
    wire [127:0] rsp_rdata;
    wire mem_valid, mem_write;
    wire [23:0] mem_addr;
    wire [31:0] mem_wdata;
    wire [3:0] mem_byte_en;
    wire mem_ready;
    wire mem_rvalid;
    wire [31:0] mem_rdata;
    reg model_rvalid = 0;
    reg [31:0] model_rdata = 0;
    wire [31:0] backend_reads, backend_writes, backend_refreshes;

    // Four disjoint fixtures: WRAM, VRAM, BIOS and the end of physical SDRAM.
    reg [31:0] memory [0:16383];
    function automatic integer slot(input reg [23:0] address);
        case (address[23:12])
            12'h000: slot = integer'(address[11:0]);
            12'h080: slot = 4096 + integer'(address[11:0]);
            12'h400: slot = 8192 + integer'(address[11:0]);
            12'hFFF: slot = 12288 + integer'(address[11:0]);
            default: begin slot = 0; $fatal(1, "Unexpected memory address %h", address); end
        endcase
    endfunction
    function automatic [23:0] physical_word(input integer index);
        case (index / 4096)
            0: physical_word = 24'(index);
            1: physical_word = 24'h080000 + 24'(index % 4096);
            2: physical_word = 24'h400000 + 24'(index % 4096);
            3: physical_word = 24'hFFF000 + 24'(index % 4096);
        endcase
    endfunction
    function automatic [7:0] initial_byte(input integer address);
        initial_byte = 8'(address * 17 + (address >> 8) * 23 + 'h5A);
    endfunction

    reg [31:0] random_state = 32'h50535803;
    reg pending = 0;
    integer delay_count = 0;
    reg [31:0] pending_data;
    generate if (!CONTROLLER) begin : simple_backend
        psx_mem_bridge dut (.*);
        assign mem_ready = !rst && !pending && random_state[3:0] != 0;
        assign mem_rvalid = model_rvalid;
        assign mem_rdata = model_rdata;
        assign backend_reads = 0; assign backend_writes = 0; assign backend_refreshes = 0;
    end else if (CONTROLLER == 1) begin : controller_backend
        wire [1:0] dfi_ras_n, dfi_cas_n, dfi_we_n;
        wire [25:0] dfi_addr;
        wire [3:0] dfi_ba, dfi_mask;
        wire dfi_cke, dfi_wrdata_en, dfi_rddata_en, dfi_rvalid;
        wire [31:0] dfi_wrdata, dfi_rdata;
        wire initialized;
        psx_sdram #(.T_INIT(12), .T_REFI(37)) memory (
            .clk(clk), .rst(rst), .initialized(initialized),
            .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write),
            .req_addr(req_addr), .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
            .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
            .dfi_ras_n(dfi_ras_n), .dfi_cas_n(dfi_cas_n), .dfi_we_n(dfi_we_n),
            .dfi_addr(dfi_addr), .dfi_ba(dfi_ba), .dfi_cke(dfi_cke),
            .dfi_wrdata_en(dfi_wrdata_en), .dfi_wrdata(dfi_wrdata), .dfi_wrdata_mask(dfi_mask),
            .dfi_rddata_en(dfi_rddata_en), .dfi_rddata(dfi_rdata), .dfi_rddata_valid(dfi_rvalid)
        );
        // Observe the wrapper's accepted backend beats without exposing debug
        // ports in the synthesis interface.
        assign mem_valid = memory.mem_valid;
        assign mem_ready = memory.mem_ready;
        assign mem_write = memory.mem_write;
        assign mem_addr = memory.mem_addr;
        assign mem_wdata = memory.mem_wdata;
        assign mem_byte_en = memory.mem_byte_en;
        assign mem_rvalid = memory.mem_rvalid;
        assign mem_rdata = memory.mem_rdata;
        sdr_command_model model (
            .clk(clk), .rst(rst), .ras_n(dfi_ras_n), .cas_n(dfi_cas_n), .we_n(dfi_we_n),
            .address(dfi_addr), .bank(dfi_ba), .cke(dfi_cke),
            .wrdata_en(dfi_wrdata_en), .rddata_en(dfi_rddata_en), .wrdata(dfi_wrdata),
            .wrdata_mask(dfi_mask), .rddata(dfi_rdata), .rddata_valid(dfi_rvalid)
        );
        assign backend_reads = model.reads;
        assign backend_writes = model.writes;
        assign backend_refreshes = model.refreshes;
        always @(posedge clk) if (!rst && mem_valid && mem_ready) begin
            if (!initialized) $fatal(1, "Transfer accepted before SDRAM initialization");
            if (model.word_address !== mem_addr) $fatal(1, "DFI physical address mismatch");
        end
    end else begin : pin_backend
        wire initialized;
        wire [12:0] dram_a;
        wire [1:0] dram_ba, dram_dqm;
        wire dram_ras_n, dram_cas_n, dram_we_n, dram_cke, dram_clk;
        wire [15:0] dram_dq;
        psx_sdram_pins #(.T_INIT(12), .T_REFI(37)) pads (
            .clk_sys(clk), .clk_mem(clk_mem), .clk_dram(clk_dram), .reset_async(rst),
            .initialized(initialized), .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write),
            .req_addr(req_addr), .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
            .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
            .dram_a(dram_a), .dram_ba(dram_ba), .dram_dqm(dram_dqm),
            .dram_ras_n(dram_ras_n), .dram_cas_n(dram_cas_n), .dram_we_n(dram_we_n),
            .dram_cke(dram_cke), .dram_dq(dram_dq), .dram_clk(dram_clk)
        );
        sdr_pin_model #(.T_AC(PIN_T_AC), .T_AC_SECOND(PIN_T_AC_SECOND), .T_HZ(PIN_T_HZ)) model (
            .clk(dram_clk), .cke(dram_cke), .ras_n(dram_ras_n), .cas_n(dram_cas_n), .we_n(dram_we_n),
            .address(dram_a), .bank(dram_ba), .dm(dram_dqm), .dq(dram_dq), .host_oe(pads.phy.oe_reg)
        );
        assign mem_valid = pads.memory.mem_valid;
        assign mem_ready = pads.memory.mem_ready;
        assign mem_write = pads.memory.mem_write;
        assign mem_addr = pads.memory.mem_addr;
        assign mem_wdata = pads.memory.mem_wdata;
        assign mem_byte_en = pads.memory.mem_byte_en;
        assign mem_rvalid = pads.memory.mem_rvalid;
        assign mem_rdata = pads.memory.mem_rdata;
        assign backend_reads = model.reads;
        assign backend_writes = model.writes;
        assign backend_refreshes = model.refreshes;
    end endgenerate

    wire [1:0] profile_port = req_addr >= 25'h1000000 ? 2'd3 :
                              req_addr >= 25'h800000 ? 2'd2 :
                              req_addr >= 25'h100000 ? 2'd1 : 2'd0;
    wire [3:0] profile_issue = req_valid && req_ready ? (4'b1 << profile_port) : 0;
    wire [31:0] profile_cycles, profile_busy_cycles, profile_errors;
    wire [3:0][31:0] profile_requests, profile_completions, profile_bytes, profile_wait;
    wire [3:0][31:0] profile_latency_sum, profile_latency_max;
    wire profile_busy;
    psx_mem_profile profiler (
        .clk(clk), .rst(rst), .clear(1'b0), .issue(profile_issue),
        .issue_bytes({3'b0, req_halfwords, 1'b0}), .complete(rsp_valid && rsp_ready), .waiting(4'b0),
        .busy(profile_busy), .cycles(profile_cycles), .busy_cycles(profile_busy_cycles),
        .protocol_errors(profile_errors), .requests(profile_requests), .completions(profile_completions),
        .bytes_issued(profile_bytes), .wait_cycles(profile_wait),
        .latency_sum(profile_latency_sum), .latency_max(profile_latency_max)
    );
    integer beats_seen = 0, testcase = 0;
    reg invalid_case = 0;
    reg [24:0] active_address;
    reg [127:0] stalled_payload;
    reg stalled_error;
    reg stalled = 0;
    reg mem_stalled = 0;
    reg [60:0] held_request;

    always @(posedge clk) begin
        random_state <= random_state * 32'd1664525 + 32'd1013904223;
        model_rvalid <= 0;
        if (rst) begin pending <= 0; delay_count <= 0; end
        else begin
            if (!CONTROLLER && pending) begin
                if (delay_count == 0) begin
                    model_rvalid <= 1; model_rdata <= pending_data; pending <= 0;
                end else delay_count <= delay_count - 1;
            end
            if (mem_valid && mem_ready) begin
                if (invalid_case) $fatal(1, "Invalid transaction reached memory");
                if (mem_addr !== active_address[24:1] + 24'(beats_seen))
                    $fatal(1, "Address/ordering mismatch in case %0d", testcase);
                beats_seen = beats_seen + 1;
                if (!CONTROLLER && mem_write) begin
                    for (integer b = 0; b < 4; b = b + 1)
                        if (mem_byte_en[b]) memory[slot(mem_addr)][b*8 +: 8] <= mem_wdata[b*8 +: 8];
                end else if (!CONTROLLER) begin
                    pending_data <= memory[slot(mem_addr)];
                    pending <= 1; delay_count <= integer'(random_state[8:6]) + 1;
                end
            end
        end
        if (!rst && mem_stalled &&
            (!mem_valid || {mem_write, mem_addr, mem_wdata, mem_byte_en} !== held_request))
            $fatal(1, "Memory request changed under backpressure");
        mem_stalled <= !rst && mem_valid && !mem_ready;
        held_request <= {mem_write, mem_addr, mem_wdata, mem_byte_en};
        if (!rst && stalled && (!rsp_valid || rsp_rdata !== stalled_payload || rsp_error !== stalled_error))
            $fatal(1, "Response changed under backpressure");
        stalled <= !rst && rsp_valid && !rsp_ready;
        stalled_payload <= rsp_rdata; stalled_error <= rsp_error;
    end

    integer file, fields;
    reg [8191:0] vector_path;
    reg [24:0] address;
    reg [3:0] count;
    reg write_flag, expected_error;
    reg [127:0] data, expected_data;
    reg [15:0] mask;
    integer waited, expected_beats;

    task automatic abort_pending_read;
        begin
            while (!req_ready) @(negedge clk);
            req_valid = 1; req_write = 0; req_addr = 0; req_halfwords = 8;
            invalid_case = 0; active_address = 0; beats_seen = 0;
            @(negedge clk); req_valid = 0;
            waited = 0;
            while (!(mem_valid && mem_ready)) begin
                @(negedge clk); waited = waited + 1;
                if (waited > 250) $fatal(1, "Abort-test issue timeout");
            end
            @(negedge clk); // a read has now been accepted, with its response pending
            rst = 1;
            #1;
            if (req_ready || rsp_valid || mem_valid) $fatal(1, "Reset did not gate interfaces");
            repeat (3) @(negedge clk);
            if (profile_requests != 0 || profile_completions != 0 || profile_errors != 0)
                $fatal(1, "Reset did not discard in-flight profiling epoch");
            rst = 0;
            while (!req_ready) @(negedge clk);
        end
    endtask

    initial begin
        for (integer i = 0; i < 16384; i = i + 1)
            for (integer b = 0; b < 4; b = b + 1)
                memory[i][b*8 +: 8] = initial_byte(integer'(physical_word(i))*4+b);
        if (!$value$plusargs("vectors=%s", vector_path)) $fatal(1, "Missing vector file");
        file = $fopen(vector_path, "r");
        if (!file) $fatal(1, "Cannot open vectors");
        repeat (3) @(negedge clk);
        rst = 0;
        abort_pending_read();
        while (!$feof(file)) begin
            fields = $fscanf(file, "%h %h %h %h %h %h %h\n",
                             address, count, write_flag, data, mask, expected_error, expected_data);
            if (fields != 7) $fatal(1, "Malformed vector row");
            testcase = testcase + 1;
            @(negedge clk);
            if (!req_ready) $fatal(1, "Frontend did not return idle");
            req_valid = 1; req_addr = address; req_halfwords = count;
            req_write = write_flag; req_wdata = data; req_byte_en = mask;
            invalid_case = expected_error; active_address = address; beats_seen = 0;
            @(negedge clk); req_valid = 0;
            // Poison live inputs after acceptance; only captured data may reach memory.
            req_addr = ~address; req_wdata = ~data; req_byte_en = ~mask; req_halfwords = 0;
            waited = 0;
            while (!rsp_valid) begin
                @(negedge clk); waited = waited + 1;
                if (waited > 250) $fatal(1, "Transaction timeout in case %0d", testcase);
                if (req_ready) $fatal(1, "Accepted overlap while transaction pending");
            end
            if (rsp_error !== expected_error || rsp_rdata !== expected_data)
                $fatal(1, "Response mismatch case %0d addr=%h count=%0d write=%0d got=%h expected=%h error=%b expected=%b",
                       testcase, address, count, write_flag, rsp_rdata, expected_data, rsp_error, expected_error);
            expected_beats = expected_error ? 0 : (integer'(address[0]) + integer'(count) + 1) / 2;
            if (beats_seen != expected_beats) $fatal(1, "Incorrect beat count");
            repeat (3) @(negedge clk); // hold response under backpressure
            rsp_ready = 1;
            @(negedge clk); rsp_ready = 0;
        end
        $fclose(file);
        if (profile_requests[0]+profile_requests[1]+profile_requests[2]+profile_requests[3] != testcase ||
            profile_completions[0]+profile_completions[1]+profile_completions[2]+profile_completions[3] != testcase ||
            profile_errors != 0 || profile_busy)
            $fatal(1, "Integrated profiler accounting mismatch");
        if (CONTROLLER) begin
            if (backend_refreshes < 3) $fatal(1, "Refresh coverage missing");
            $display("SDRAM commands: reads=%0d writes=%0d refreshes=%0d",
                     backend_reads, backend_writes, backend_refreshes);
        end
        $display("PASS memory bridge controller=%0d transactions=%0d cycles=%0d", CONTROLLER, testcase, profile_cycles);
        $finish;
    end
    initial begin #10000000; $fatal(1, "Simulation watchdog expired"); end
endmodule
`default_nettype wire
