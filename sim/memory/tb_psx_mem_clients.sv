`timescale 1ns/1ps
`default_nettype none
module tb_psx_mem_clients;
    parameter integer USE_COMPONENT = 0;
    localparam integer N = 4, MAX_CASES = 2048;
    wire clk, clk_mem, clk_dram;
    reg rst = 1, clk_74a = 0;
    always #6.734 clk_74a = ~clk_74a;
    reg [N-1:0] p_valid = 0, p_write = 0, p_rsp_ready = 0;
    wire [N-1:0] p_ready, p_rsp_valid, p_rsp_error;
    reg [N-1:0][24:0] p_addr = 0;
    reg [N-1:0][3:0] p_halfwords = 0;
    reg [N-1:0][127:0] p_wdata = 0;
    reg [N-1:0][15:0] p_byte_en = 0;
    wire [N-1:0][127:0] p_rsp_rdata;
    wire req_valid, req_ready, req_write, rsp_valid, rsp_ready, rsp_error, protocol_error;
    wire [24:0] req_addr;
    wire [3:0] req_halfwords;
    wire [127:0] req_wdata, rsp_rdata;
    wire [15:0] req_byte_en;
    wire initialized, client_reset, host_oe;
    wire [12:0] dram_a;
    wire [1:0] dram_ba, dram_dqm;
    wire dram_ras_n, dram_cas_n, dram_we_n, dram_cke, dram_clk;
    wire [15:0] dram_dq;
    generate if (!USE_COMPONENT) begin : external_clocks
        reg sys = 0, mem = 1;
        always #8 sys = ~sys;
        always #4 mem = ~mem;
        assign clk = sys; assign clk_mem = mem; assign clk_dram = ~mem;
        psx_mem_arb #(.PORTS(N)) arb (
            .clk(clk), .rst(client_reset), .p_valid(p_valid), .p_write(p_write), .p_ready(p_ready),
            .p_addr(p_addr), .p_halfwords(p_halfwords), .p_wdata(p_wdata), .p_byte_en(p_byte_en),
            .p_rsp_valid(p_rsp_valid), .p_rsp_ready(p_rsp_ready), .p_rsp_rdata(p_rsp_rdata), .p_rsp_error(p_rsp_error),
            .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write), .req_addr(req_addr),
            .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
            .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
            .protocol_error(protocol_error)
        );
        psx_sdram_pins #(.T_INIT(12), .T_REFI(37)) pads (
        .clk_sys(clk), .clk_mem(clk_mem), .clk_dram(clk_dram), .reset_async(rst),
        .client_reset(client_reset), .initialized(initialized), .req_valid(req_valid), .req_ready(req_ready), .req_write(req_write),
        .req_addr(req_addr), .req_halfwords(req_halfwords), .req_wdata(req_wdata), .req_byte_en(req_byte_en),
        .rsp_valid(rsp_valid), .rsp_ready(rsp_ready), .rsp_rdata(rsp_rdata), .rsp_error(rsp_error),
        .dram_a(dram_a), .dram_ba(dram_ba), .dram_dqm(dram_dqm),
        .dram_ras_n(dram_ras_n), .dram_cas_n(dram_cas_n), .dram_we_n(dram_we_n),
        .dram_cke(dram_cke), .dram_dq(dram_dq), .dram_clk(dram_clk)
    );
        assign host_oe = pads.phy.oe_reg;
    end else begin : component
        psx_mem_pocket #(.PORTS(N), .T_INIT(12), .T_REFI(37)) memory (
            .clk_74a(clk_74a), .reset_async(rst), .client_clk(clk), .client_reset(client_reset),
            .initialized(initialized), .protocol_error(protocol_error),
            .p_valid(p_valid), .p_write(p_write), .p_ready(p_ready), .p_addr(p_addr),
            .p_halfwords(p_halfwords), .p_wdata(p_wdata), .p_byte_en(p_byte_en),
            .p_rsp_valid(p_rsp_valid), .p_rsp_ready(p_rsp_ready), .p_rsp_rdata(p_rsp_rdata), .p_rsp_error(p_rsp_error),
            .dram_a(dram_a), .dram_ba(dram_ba), .dram_dqm(dram_dqm),
            .dram_ras_n(dram_ras_n), .dram_cas_n(dram_cas_n), .dram_we_n(dram_we_n),
            .dram_cke(dram_cke), .dram_dq(dram_dq), .dram_clk(dram_clk)
        );
        assign clk_mem = memory.clk_mem; assign clk_dram = memory.clk_dram;
        assign host_oe = memory.pads.phy.oe_reg;
        assign req_valid = memory.req_valid; assign req_ready = memory.req_ready;
        assign req_write = memory.req_write; assign req_addr = memory.req_addr;
        assign req_halfwords = memory.req_halfwords; assign req_wdata = memory.req_wdata;
        assign req_byte_en = memory.req_byte_en; assign rsp_valid = memory.rsp_valid;
        assign rsp_ready = memory.rsp_ready; assign rsp_rdata = memory.rsp_rdata; assign rsp_error = memory.rsp_error;
    end endgenerate
    sdr_pin_model #(.T_AC(5.5), .T_AC_SECOND(2.5), .T_HZ(6.0)) chip (
        .clk(dram_clk), .cke(dram_cke), .ras_n(dram_ras_n), .cas_n(dram_cas_n), .we_n(dram_we_n),
        .address(dram_a), .bank(dram_ba), .dm(dram_dqm), .dq(dram_dq), .host_oe(host_oe)
    );

    reg [24:0] addresses [N][MAX_CASES];
    reg [3:0] lengths [N][MAX_CASES];
    reg writes [N][MAX_CASES], errors [N][MAX_CASES];
    reg [127:0] data [N][MAX_CASES], expected [N][MAX_CASES];
    reg [15:0] masks [N][MAX_CASES];
    integer count [N], cursor [N], waited [N];
    reg [N-1:0] busy = 0;
    integer completed = 0, accepted = 0, cycle = 0, others_while_held = 0;
    integer max_wait = 0, expected_total = 0, first_grants = 0;
    integer aborted = 0;
    reg reset_test_done = 0;
    reg directed_done = 0;
    reg [31:0] random_state = 32'h50535804;
    reg stalled = 0;
    reg [173:0] held;
    reg [N-1:0] response_stalled = 0;
    reg [N-1:0][128:0] held_responses;

    always @(negedge clk) if (!rst && directed_done) begin
        for (integer port = 0; port < N; port = port + 1) begin
            p_valid[port] = !busy[port] && cursor[port] < count[port];
            if (cursor[port] < count[port]) begin
                p_addr[port] = addresses[port][cursor[port]];
                p_halfwords[port] = lengths[port][cursor[port]];
                p_write[port] = writes[port][cursor[port]];
                p_wdata[port] = data[port][cursor[port]];
                p_byte_en[port] = masks[port][cursor[port]];
            end
            p_rsp_ready[port] = random_state[port] || random_state[port+8];
        end
        // Hold client 0's first response while other clients continue.
        if (cycle < 350) p_rsp_ready[0] = 0;
    end
    always @(posedge clk) if (!rst && directed_done) begin
        cycle = cycle + 1;
        random_state <= random_state * 32'd1664525 + 32'd1013904223;
        if (protocol_error) $fatal(1, "Arbiter protocol error");
        if ((p_ready & (p_ready - 1'b1)) != 0) $fatal(1, "Multiple request grants");
        if (stalled && (!req_valid || {req_write,req_addr,req_halfwords,req_wdata,req_byte_en} !== held))
            $fatal(1, "Arbitrated request changed under memory backpressure");
        stalled <= req_valid && !req_ready;
        held <= {req_write,req_addr,req_halfwords,req_wdata,req_byte_en};
        for (integer port = 0; port < N; port = port + 1) begin
            if (response_stalled[port] && (!p_rsp_valid[port] ||
                {p_rsp_error[port],p_rsp_rdata[port]} !== held_responses[port]))
                $fatal(1, "Client %0d response changed under backpressure", port);
            response_stalled[port] <= p_rsp_valid[port] && !p_rsp_ready[port];
            held_responses[port] <= {p_rsp_error[port],p_rsp_rdata[port]};
            if (p_valid[port] && p_ready[port]) begin
                if (busy[port]) $fatal(1, "Accepted a second outstanding request");
                if (first_grants < N && port != first_grants) $fatal(1, "Initial round-robin order mismatch");
                first_grants = first_grants + 1;
                accepted = accepted + 1; busy[port] = 1; waited[port] = 0;
            end else if (p_valid[port]) begin
                waited[port] = waited[port] + 1;
                if (waited[port] > max_wait) max_wait = waited[port];
                if (waited[port] > 1000) $fatal(1, "Request starvation");
            end
            if (p_rsp_valid[port] && p_rsp_ready[port]) begin
                if (!busy[port] || p_rsp_error[port] !== errors[port][cursor[port]] ||
                    p_rsp_rdata[port] !== expected[port][cursor[port]])
                    $fatal(1, "Response ownership/data mismatch client=%0d case=%0d", port, cursor[port]);
                if (port != 0 && p_rsp_valid[0] && !p_rsp_ready[0]) others_while_held = others_while_held + 1;
                cursor[port] = cursor[port] + 1; busy[port] = 0; completed = completed + 1;
            end
        end
    end
    integer file, fields;
    reg [8191:0] directory;
    string filename;
    reg [24:0] loaded_address;
    reg [3:0] loaded_length;
    reg loaded_write, loaded_error;
    reg [127:0] loaded_data, loaded_expected;
    reg [15:0] loaded_mask;
    reg [127:0] shared_data, masked_data, restore_data;
    localparam [24:0] SHARED_ADDRESS = 25'd1023;
    function automatic [7:0] initial_byte(input integer address);
        initial_byte = 8'(address*17 + (address >> 8)*23 + 'h5A);
    endfunction
    task automatic directed_request(
        input integer port,
        input reg write_flag,
        input reg [127:0] payload,
        input reg [15:0] mask,
        input reg [127:0] expected_data,
        input reg consume
    );
        begin
            @(negedge clk);
            p_valid[port] = 1; p_write[port] = write_flag; p_addr[port] = SHARED_ADDRESS;
            p_halfwords[port] = 8; p_wdata[port] = payload; p_byte_en[port] = mask; p_rsp_ready[port] = 0;
            #1;
            while (!p_ready[port]) begin @(negedge clk); #1; end
            @(negedge clk); p_valid[port] = 0;
            while (!p_rsp_valid[port]) @(negedge clk);
            if (p_rsp_error[port] || p_rsp_rdata[port] !== expected_data)
                $fatal(1, "Cross-client shared-memory mismatch client=%0d", port);
            if (consume) begin
                p_rsp_ready[port] = 1;
                @(negedge clk); p_rsp_ready[port] = 0;
            end
        end
    endtask
    initial begin
        if (!$value$plusargs("programs=%s", directory)) $fatal(1, "Missing programs directory");
        for (integer port = 0; port < N; port = port + 1) begin
            count[port] = 0; cursor[port] = 0; waited[port] = 0;
            filename = $sformatf("%0s/client-%0d.txt", directory, port);
            file = $fopen(filename, "r");
            if (!file) $fatal(1, "Missing client program");
            while (!$feof(file)) begin
                if (count[port] == MAX_CASES) $fatal(1, "Client program too large");
                fields = $fscanf(file, "%h %h %h %h %h %h %h\n",
                    loaded_address, loaded_length, loaded_write, loaded_data, loaded_mask, loaded_error, loaded_expected);
                if (fields != 7) $fatal(1, "Malformed client vector");
                addresses[port][count[port]] = loaded_address; lengths[port][count[port]] = loaded_length;
                writes[port][count[port]] = loaded_write; data[port][count[port]] = loaded_data;
                masks[port][count[port]] = loaded_mask; errors[port][count[port]] = loaded_error;
                expected[port][count[port]] = loaded_expected;
                count[port] = count[port] + 1;
            end
            $fclose(file); expected_total = expected_total + count[port];
        end
        repeat (4) @(negedge clk); rst = 0;
        shared_data = 128'h0123456789abcdef_fedcba9876543210;
        // A writer's response stays buffered while another client reads its data.
        directed_request(0, 1, shared_data, 16'hFFFF, 0, 0);
        directed_request(1, 0, 0, 0, shared_data, 1);
        if (!p_rsp_valid[0]) $fatal(1, "Writer response lost while another client read");
        masked_data = 0; masked_data[7:0] = 8'hAA; masked_data[127:120] = 8'h55;
        directed_request(2, 1, masked_data, 16'h8001, 0, 1);
        shared_data[7:0] = 8'hAA; shared_data[127:120] = 8'h55;
        directed_request(3, 0, 0, 0, shared_data, 1);
        if (!p_rsp_valid[0]) $fatal(1, "Writer response lost while other clients progressed");
        p_rsp_ready[0] = 1; @(negedge clk); p_rsp_ready[0] = 0;
        for (integer byte_index = 0; byte_index < 16; byte_index = byte_index + 1)
            restore_data[byte_index*8 +: 8] = initial_byte(integer'(SHARED_ADDRESS)*2 + byte_index);
        directed_request(2, 1, restore_data, 16'hFFFF, 0, 1);
        rst = 1; p_valid = 0; p_rsp_ready = 0;
        repeat (5) @(negedge clk);
        rst = 0; directed_done = 1;
        while (completed < expected_total) @(negedge clk);
        if (accepted != expected_total || others_while_held == 0 || busy != 0 || !reset_test_done)
            $fatal(1, "Missing transaction or response isolation coverage");
        $display("PASS component=%0d clients=%0d transactions=%0d max_request_wait=%0d others_while_held=%0d aborted=%0d",
                 USE_COMPONENT, N, completed, max_wait, others_while_held, aborted);
        $finish;
    end
    initial begin
        wait (rst == 0 && directed_done);
        while (!(p_rsp_valid[0] && !p_rsp_ready[0] && rsp_ready)) @(negedge clk);
        rst = 1; p_valid = 0; p_rsp_ready = 0;
        aborted = accepted-completed; accepted = completed; busy = 0;
        stalled = 0; response_stalled = 0; first_grants = 0;
        for (integer port = 0; port < N; port = port + 1) waited[port] = 0;
        #1;
        if (p_rsp_valid != 0 || p_ready != 0) $fatal(1, "Client interfaces not gated during reset");
        repeat (5) @(negedge clk);
        rst = 0; reset_test_done = 1;
    end
    initial begin #10000000; $fatal(1, "Client simulation watchdog expired"); end
endmodule
`default_nettype wire
