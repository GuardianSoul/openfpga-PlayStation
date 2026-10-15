`timescale 1ns/1ps
`default_nettype none
module tb_psx_pgate_memory;
    parameter integer CORE_HALF = 10;
    localparam integer MAX_CASES = 4096;
    reg clk_74a = 0, clk_core = 0, reset_async = 1;
    always #6.734 clk_74a = !clk_74a;
    initial begin #3; forever #(CORE_HALF) clk_core = !clk_core; end
    wire core_reset, initialized, protocol_error;
    reg wram_read = 0, wram_write = 0, bios_read = 0;
    reg [20:0] a = 0;
    reg [4:0] bytes = 0;
    reg [31:0] dout = 0;
    wire wram_ack, bios_ack, wram_error, bios_error;
    wire [127:0] din;
    reg vram_read = 0, vram_write = 0, vram_flush = 0;
    reg [18:0] vram_addr = 0;
    reg [2:0] vram_halfwords = 0;
    reg [15:0] vram_dout = 0;
    wire vram_read_ack, vram_write_ack, vram_read_error, vram_write_error;
    wire [63:0] vram_din;
    wire [12:0] dram_a;
    wire [1:0] dram_ba, dram_dqm;
    wire dram_ras_n, dram_cas_n, dram_we_n, dram_cke, dram_clk;
    wire [15:0] dram_dq;
    psx_pgate_memory #(.T_INIT(12), .T_REFI(37)) dut (.*);
    sdr_pin_model #(.T_AC(5.5), .T_AC_SECOND(2.5), .T_HZ(6.0), .EDGE_REGIONS(1)) chip (
        .clk(dram_clk), .cke(dram_cke), .ras_n(dram_ras_n), .cas_n(dram_cas_n), .we_n(dram_we_n),
        .address(dram_a), .bank(dram_ba), .dm(dram_dqm), .dq(dram_dq), .host_oe(dut.memory.pads.phy.oe_reg)
    );
    integer sys_kind [MAX_CASES], sys_length [MAX_CASES], sys_count = 0, sys_index = 0;
    reg [20:0] sys_address [MAX_CASES];
    reg [31:0] sys_input [MAX_CASES];
    reg sys_error [MAX_CASES];
    reg [127:0] sys_expected [MAX_CASES], sys_payload [MAX_CASES];
    reg [24:0] sys_physical [MAX_CASES];
    reg [3:0] sys_halfwords [MAX_CASES];
    reg [15:0] sys_mask [MAX_CASES];
    integer gpu_kind [MAX_CASES], gpu_length [MAX_CASES], gpu_count = 0, gpu_index = 0;
    reg [18:0] gpu_address [MAX_CASES];
    reg [15:0] gpu_input [MAX_CASES];
    reg gpu_error [MAX_CASES];
    reg [63:0] gpu_expected [MAX_CASES];
    reg [24:0] gpu_physical [MAX_CASES];
    reg [3:0] gpu_halfwords [MAX_CASES];
    integer sys_requests = 0, gpu_requests = 0, sys_completed = 0, gpu_completed = 0, flushes = 0;
    integer concurrent_cycles = 0, reset_cases = 0, directed_requests = 0;
    reg running = 0, sys_done = 0, gpu_done = 0;
    reg [127:0] saved_din = 0;
    reg [63:0] saved_gpu = 0;
    reg directed = 0;

    always @(posedge clk_core) if (running && !core_reset) begin
        if (protocol_error) $fatal(1, "Unexpected legacy adapter protocol error");
        if (dut.adapter.sys_state != 0 && dut.adapter.gpu_state != 0) concurrent_cycles = concurrent_cycles + 1;
        if (dut.s_valid[0] && dut.s_ready[0]) begin
            sys_requests = sys_requests + 1;
            if ({dut.s_write[0], dut.s_addr[0], dut.s_halfwords[0], dut.s_byte_en[0], dut.s_wdata[0]} !==
                {sys_kind[sys_index] == 1, sys_physical[sys_index], sys_halfwords[sys_index], sys_mask[sys_index], sys_payload[sys_index]})
                $fatal(1, "System request normalization mismatch case=%0d", sys_index);
        end
        if (dut.s_valid[1] && dut.s_ready[1]) begin
            gpu_requests = gpu_requests + 1;
            if (gpu_kind[gpu_index] == 2 ||
                {dut.s_write[1], dut.s_addr[1], dut.s_halfwords[1], dut.s_byte_en[1], dut.s_wdata[1]} !==
                {gpu_kind[gpu_index] != 0, gpu_physical[gpu_index], gpu_halfwords[gpu_index], 16'h0003, 112'b0, gpu_input[gpu_index]})
                $fatal(1, "GPU request duplicated or normalization mismatch case=%0d", gpu_index);
        end
    end
    always @(posedge clk_core) if (directed && !core_reset && dut.s_valid[0] && dut.s_ready[0])
        directed_requests = directed_requests + 1;
    task automatic reset_memory;
        begin
            #1; reset_async = 1;
            wram_read = 0; wram_write = 0; bios_read = 0; vram_read = 0; vram_write = 0; vram_flush = 0;
            #0.1;
            if (!core_reset || wram_ack || bios_ack || vram_read_ack || vram_write_ack || dut.s_valid != 0 || dut.p_valid != 0)
                $fatal(1, "Legacy transfers not gated on reset");
            repeat (6) @(negedge clk_core);
            reset_async = 0;
            while (core_reset || !initialized) @(negedge clk_core);
            repeat (10) @(negedge clk_core);
            if (!wram_ack || !bios_ack || vram_read_ack || vram_write_ack || din != 0 || vram_din != 0 || protocol_error)
                $fatal(1, "Legacy state survived reset");
        end
    endtask
    integer file, fields, error_field, temp_kind, temp_address, temp_count, temp_data;
    reg [127:0] temp_expected, temp_payload;
    reg [24:0] temp_physical;
    reg [3:0] temp_halfwords;
    reg [15:0] temp_mask;
    reg [8191:0] directory;
    string filename;
    initial begin
        if (!$value$plusargs("programs=%s", directory)) $fatal(1, "Missing pgate programs directory");
        filename = $sformatf("%0s/system.txt", directory); file = $fopen(filename, "r");
        if (!file) $fatal(1, "Missing system vectors");
        while (!$feof(file)) begin
            fields = $fscanf(file, "%h %h %h %h %h %h %h %h %h %h\n",
                temp_kind, temp_address, temp_count, temp_data, error_field, temp_expected, temp_physical, temp_halfwords, temp_mask, temp_payload);
            if (fields != 10 || sys_count == MAX_CASES) $fatal(1, "Bad system vector");
            sys_kind[sys_count] = temp_kind; sys_address[sys_count] = 21'(temp_address);
            sys_length[sys_count] = temp_count; sys_input[sys_count] = 32'(temp_data); sys_error[sys_count] = 1'(error_field);
            sys_expected[sys_count] = temp_expected; sys_physical[sys_count] = temp_physical;
            sys_halfwords[sys_count] = temp_halfwords; sys_mask[sys_count] = temp_mask; sys_payload[sys_count] = temp_payload;
            sys_count = sys_count + 1;
        end
        $fclose(file);
        filename = $sformatf("%0s/gpu.txt", directory); file = $fopen(filename, "r");
        if (!file) $fatal(1, "Missing GPU vectors");
        while (!$feof(file)) begin
            fields = $fscanf(file, "%h %h %h %h %h %h %h %h\n",
                temp_kind, temp_address, temp_count, temp_data, error_field, temp_expected, temp_physical, temp_halfwords);
            if (fields != 8 || gpu_count == MAX_CASES) $fatal(1, "Bad GPU vector");
            gpu_kind[gpu_count] = temp_kind; gpu_address[gpu_count] = 19'(temp_address);
            gpu_length[gpu_count] = temp_count; gpu_input[gpu_count] = 16'(temp_data); gpu_error[gpu_count] = 1'(error_field);
            gpu_expected[gpu_count] = temp_expected[63:0]; gpu_physical[gpu_count] = temp_physical; gpu_halfwords[gpu_count] = temp_halfwords;
            gpu_count = gpu_count + 1;
        end
        $fclose(file);
        reset_memory();
        // Abort reads at two different stages. SDRAM contents and chip state
        // persist; only the host logic resets. No write rollback is assumed.
        @(negedge clk_core); wram_read = 1; a = 128; bytes = 4;
        vram_read = 1; vram_addr = 64; vram_halfwords = 2;
        @(negedge clk_core); wram_read = 0;
        while (!dut.memory.pads.memory.bridge.mem_valid) @(negedge clk_core);
        reset_memory(); reset_cases = reset_cases + 1;
        @(negedge clk_core); bios_read = 1; a = 128; bytes = 4;
        @(negedge clk_core); bios_read = 0;
        wait (dut.s_rsp_valid[0]);
        reset_memory(); reset_cases = reset_cases + 1;
        // The CD DMA may issue its next write in the very cycle it sees the
        // preceding completion. Accept it while the old response is consumed.
        directed = 1;
        @(negedge clk_core); wram_read = 1; a = 256; bytes = 4;
        @(negedge clk_core); wram_read = 0;
        while (!wram_ack) @(negedge clk_core);
        saved_din = din;
        wram_write = 1; a = 256; bytes = 4; dout = 32'h5F6F7F8F;
        @(negedge clk_core); wram_write = 0;
        if (wram_ack || bios_ack) $fatal(1, "Completion-edge write was not captured");
        while (!wram_ack) @(negedge clk_core);
        @(negedge clk_core); wram_read = 1; a = 256; bytes = 4;
        @(negedge clk_core); wram_read = 0;
        while (!wram_ack) @(negedge clk_core);
        if (din !== 128'h5F6F7F8F) $fatal(1, "Completion-edge write visibility failed");
        // Restore the byte oracle's initial contents before its program starts.
        @(negedge clk_core); wram_write = 1; a = 256; bytes = 4; dout = 32'hA4938271;
        @(negedge clk_core); wram_write = 0;
        while (!wram_ack) @(negedge clk_core);
        @(negedge clk_core); wram_read = 1; a = 256; bytes = 4;
        @(negedge clk_core); wram_read = 0;
        while (!wram_ack) @(negedge clk_core);
        if (din !== 128'hA4938271) $fatal(1, "Oracle fixture restoration failed");
        @(negedge clk_core); directed = 0;
        if (directed_requests != 5) $fatal(1, "Completion-edge transaction count mismatch");
        reset_memory(); saved_din = 0;
        running = 1;
        fork
            begin : system_program
                for (sys_index = 0; sys_index < sys_count; sys_index = sys_index + 1) begin
                    @(negedge clk_core);
                    if (!wram_ack || !bios_ack || din !== saved_din) $fatal(1, "Missing idle readiness or saved system data");
                    a = sys_address[sys_index]; bytes = 5'(sys_length[sys_index]); dout = sys_input[sys_index];
                    wram_read = sys_kind[sys_index] == 0; wram_write = sys_kind[sys_index] == 1; bios_read = sys_kind[sys_index] == 2;
                    @(negedge clk_core);
                    wram_read = 0; wram_write = 0; bios_read = 0; a = ~a; bytes = 0; dout = ~dout;
                    if (wram_ack || bios_ack) $fatal(1, "System idle ack leaked into a pending transaction");
                    while (!(sys_kind[sys_index] == 2 ? bios_ack : wram_ack)) @(negedge clk_core);
                    if ((sys_kind[sys_index] == 2 ? wram_ack : bios_ack) ||
                        (sys_kind[sys_index] == 2 ? bios_error : wram_error) !== sys_error[sys_index])
                        $fatal(1, "Wrong system response owner/error case=%0d", sys_index);
                    if (sys_kind[sys_index] != 1) begin
                        if (din !== sys_expected[sys_index]) $fatal(1, "Legacy system read mismatch case=%0d got=%h expected=%h", sys_index, din, sys_expected[sys_index]);
                        saved_din = sys_expected[sys_index];
                    end else if (din !== saved_din) $fatal(1, "Write replaced saved read data");
                    sys_completed = sys_completed + 1;
                end
                sys_done = 1;
            end
            begin : gpu_program
                for (gpu_index = 0; gpu_index < gpu_count; gpu_index = gpu_index + 1) begin
                    @(negedge clk_core);
                    if (vram_read_ack || vram_write_ack || vram_din !== saved_gpu) $fatal(1, "Stray GPU ack or saved data corruption");
                    vram_addr = gpu_address[gpu_index]; vram_halfwords = 3'(gpu_length[gpu_index]); vram_dout = gpu_input[gpu_index];
                    vram_read = gpu_kind[gpu_index] == 0;
                    vram_write = gpu_kind[gpu_index] == 1 || gpu_kind[gpu_index] == 3;
                    vram_flush = gpu_kind[gpu_index] == 2 || gpu_kind[gpu_index] == 3;
                    @(negedge clk_core);
                    // Test both event-only and held requests. Pulse cases poison
                    // their fields after capture; held cases retain them until ack.
                    if (gpu_index % 7 == 0 && gpu_kind[gpu_index] != 2) begin
                        vram_read = 0; vram_write = 0; vram_flush = 0; vram_addr = ~vram_addr;
                        vram_halfwords = 0; vram_dout = ~vram_dout;
                    end
                    while (!(gpu_kind[gpu_index] == 0 ? vram_read_ack : vram_write_ack)) @(negedge clk_core);
                    if ((gpu_kind[gpu_index] == 0 ? vram_write_ack : vram_read_ack) ||
                        (gpu_kind[gpu_index] == 0 ? vram_read_error : vram_write_error) !== gpu_error[gpu_index])
                        $fatal(1, "Wrong GPU response owner/error case=%0d", gpu_index);
                    if (gpu_kind[gpu_index] == 0) begin
                        if (vram_din !== gpu_expected[gpu_index]) $fatal(1, "GPU read mismatch case=%0d", gpu_index);
                        saved_gpu = gpu_expected[gpu_index];
                    end
                    if (gpu_kind[gpu_index] == 2) flushes = flushes + 1;
                    vram_read = 0; vram_write = 0; vram_flush = 0;
                    gpu_completed = gpu_completed + 1;
                end
                gpu_done = 1;
            end
        join
        repeat (20) @(negedge clk_core);
        if (!sys_done || !gpu_done || sys_requests != sys_count || gpu_requests != gpu_count - flushes || concurrent_cycles < 100)
            $fatal(1, "Legacy accounting/concurrency failure sys=%0d gpu=%0d flushes=%0d concurrent=%0d", sys_requests, gpu_requests, flushes, concurrent_cycles);
        running = 0;
        // Colliding event requests are diagnosed without issuing either write.
        @(negedge clk_core); wram_read = 1; bios_read = 1;
        @(negedge clk_core); wram_read = 0; bios_read = 0;
        if (!protocol_error || dut.s_valid != 0) $fatal(1, "Missing collision diagnostic");
        reset_memory();
        // An event while busy must be diagnosed, not overwrite the pending read.
        directed_requests = 0; directed = 1;
        @(negedge clk_core); wram_read = 1; a = 32; bytes = 4;
        @(negedge clk_core); wram_read = 0; wram_write = 1; dout = 32'hDEADBEEF;
        @(negedge clk_core); wram_write = 0;
        if (!protocol_error) $fatal(1, "Busy event did not set protocol error");
        while (!wram_ack) @(negedge clk_core);
        repeat (10) @(negedge clk_core);
        directed = 0;
        if (directed_requests != 1) $fatal(1, "Busy event overwrote or duplicated request");
        reset_memory();
        @(negedge clk_core); vram_read = 1; vram_write = 1; vram_addr = 32; vram_halfwords = 1;
        @(negedge clk_core);
        if (!protocol_error || !vram_read_ack || !vram_write_ack || !vram_read_error || !vram_write_error ||
            vram_din != 0 || dut.s_valid != 0) $fatal(1, "GPU collision was not rejected locally");
        vram_read = 0; vram_write = 0;
        reset_memory();
        $display("PASS pgate core_half=%0d system=%0d gpu=%0d flushes=%0d concurrent_cycles=%0d resets=%0d",
                 CORE_HALF, sys_completed, gpu_completed, flushes, concurrent_cycles, reset_cases);
        $finish;
    end
    initial begin #20000000; $fatal(1, "Pgate integration test timeout"); end
endmodule
`default_nettype wire
