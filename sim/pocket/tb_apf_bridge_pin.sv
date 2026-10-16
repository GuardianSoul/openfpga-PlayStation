`timescale 1ps/1ps
// Actual APF adapter pins. The host, core response and serial receiver are
// independent port-level models; no DUT state or private register is read.
module tb_apf_bridge_pin #(
    parameter integer HOST_HALF_PS = 13468,
    parameter integer PHASE_PS = 1,
    parameter integer INPUT_CLOCK_PS = 0,
    parameter integer INPUT_DATA_PS = 0,
    parameter integer SELECT_PS = 0,
    parameter integer RETURN_CLOCK_PS = 0,
    parameter integer RETURN_DATA_PS = 0,
    parameter integer RESPONSE_CYCLES = 12,
    parameter integer PAUSE_PS = 0,
    parameter integer CHECK_WINDOWS = 1,
    parameter integer SETUP_PS = 1000,
    parameter integer HOLD_PS = 1000,
    parameter integer CONTENTION = 0
);
    localparam integer CORE_HALF_PS = 6734;
    reg clk = 0;
    always #CORE_HALF_PS clk = ~clk;
    reg reset_n = 0, endian_little = 0;
    reg host_drive = 0, host_clock = 0, host_select = 1;
    reg [1:0] host_data = 0;
    tri pin_clock, pin_mosi, pin_miso;
    reg host_clock_driver = 1'bz, host_mosi_driver = 1'bz, host_miso_driver = 1'bz;
    reg pin_select = 1;
    // Scheduled nonblocking updates preserve edges even in late-data controls;
    // continuous-assignment inertial delays would silently filter short pulses.
    always @(host_drive or host_clock)
        host_clock_driver <= #(INPUT_CLOCK_PS) host_drive ? host_clock : 1'bz;
    always @(host_drive or host_data) begin
        host_mosi_driver <= #(INPUT_DATA_PS) host_drive ? host_data[1] : 1'bz;
        host_miso_driver <= #(INPUT_DATA_PS) host_drive ? host_data[0] : 1'bz;
    end
    always @(host_select) pin_select <= #(SELECT_PS) host_select;
    assign pin_clock = host_clock_driver;
    assign pin_mosi = host_mosi_driver;
    assign pin_miso = host_miso_driver;
    reg return_clock = 1'bz;
    reg [1:0] return_data = 2'bzz;
    always @(pin_clock) return_clock <= #(RETURN_CLOCK_PS) pin_clock;
    always @(pin_mosi or pin_miso) return_data <= #(RETURN_DATA_PS) {pin_mosi, pin_miso};

    wire [31:0] address, write_data;
    wire address_valid, rd, wr;
    reg [31:0] read_data = 32'h1957a6c3;
    io_bridge_peripheral dut (
        .clk(clk), .reset_n(reset_n), .endian_little(endian_little),
        .pmp_addr(address), .pmp_addr_valid(address_valid),
        .pmp_rd(rd), .pmp_rd_data(read_data), .pmp_wr(wr),
        .pmp_wr_data(write_data), .phy_spiclk(pin_clock),
        .phy_spimosi(pin_mosi), .phy_spimiso(pin_miso), .phy_spiss(pin_select)
    );

    function automatic [31:0] swapped(input [31:0] w);
        swapped = {w[7:0], w[15:8], w[23:16], w[31:24]};
    endfunction
    function automatic [31:0] response(input [31:0] a);
        response = (a ^ 32'h39a57c16) + {a[13:0], a[31:14]};
    endfunction

    integer writes = 0, reads = 0, aborts = 0, rx_edges = 0, tx_pairs = 0;
    integer pending_response = -1;
    reg [31:0] next_response;
    reg expect_write = 0, expect_read = 0, active = 0;
    reg [31:0] expected_address = 0, expected_write = 0;
    reg [31:0] buffered_expected = 32'h1957a6c3;
    reg previous_wr = 0, previous_rd = 0;
    always @(negedge clk) if (active) begin
        if (wr === 1'b1) begin
            if (!expect_write || previous_wr || address_valid !== 1'b1 ||
                address !== expected_address || write_data !== expected_write)
                $fatal(1, "BRIDGE_WRITE addr=%h expected=%h data=%h expected=%h", address, expected_address, write_data, expected_write);
            writes = writes + 1;
        end
        if (rd === 1'b1) begin
            if (!expect_read || previous_rd || address_valid !== 1'b1 || address !== expected_address)
                $fatal(1, "BRIDGE_READ_REQUEST addr=%h expected=%h", address, expected_address);
            if (pending_response >= 0) $fatal(1, "BRIDGE_RESPONSE overlapping logical requests");
            reads = reads + 1;
            next_response = response(address);
            pending_response = RESPONSE_CYCLES;
        end else if (pending_response == 0) begin
            read_data = next_response;
            pending_response = -1;
        end else if (pending_response > 0) pending_response = pending_response - 1;
        previous_rd = rd;
        previous_wr = wr;
    end

    time last_input_change = 0, last_select_change = 0, last_return_change = 0;
    time last_sample = 0;
    time min_input_setup = 64'h7fff_ffff_ffff_ffff;
    time min_return_setup = 64'h7fff_ffff_ffff_ffff;
    time min_return_hold = 64'h7fff_ffff_ffff_ffff;
    reg waiting_hold = 0;
    always @(pin_mosi or pin_miso) last_input_change = $time;
    always @(pin_select) last_select_change = $time;
    always @(return_data) begin
        last_return_change = $time;
        if (waiting_hold) begin
            if ($time - last_sample < min_return_hold) min_return_hold = $time - last_sample;
            waiting_hold = 0;
        end
    end
    always @(posedge pin_clock) if (active && host_drive && pin_select === 1'b0) begin
        rx_edges = rx_edges + 1;
        if ($time - last_input_change < min_input_setup) min_input_setup = $time - last_input_change;
        if (CHECK_WINDOWS && ($time-last_input_change < SETUP_PS || $time-last_select_change < SETUP_PS))
            $fatal(1, "BRIDGE_INPUT_WINDOW setup");
        #HOLD_PS;
        if (CHECK_WINDOWS && ($time-last_input_change < HOLD_PS || $time-last_select_change < HOLD_PS))
            $fatal(1, "BRIDGE_INPUT_WINDOW hold");
    end
    always @(pin_clock or pin_mosi or pin_miso) if (active && expect_read) begin
        if (pin_clock === 1'bx || pin_mosi === 1'bx || pin_miso === 1'bx)
            $fatal(1, "BRIDGE_CONTENTION resolved pin is X");
    end

    task automatic begin_frame;
        begin
            host_drive = 1;
            host_clock = 0;
            host_data = 0;
            host_select = 0;
            #HOST_HALF_PS;
        end
    endtask
    task automatic end_frame;
        begin
            host_select = 1;
            host_drive = 0;
            #(32 * 2 * CORE_HALF_PS);
            expect_read = 0;
            expect_write = 0;
        end
    endtask
    task automatic send_pairs(input [31:0] word_value, input integer count);
        integer k;
        begin
            for (k=0; k<count; k=k+1) begin
                host_clock = 0;
                host_data = word_value[31-2*k -: 2];
                if (PAUSE_PS != 0 && k == 5) #PAUSE_PS;
                #HOST_HALF_PS;
                host_clock = 1;
                #HOST_HALF_PS;
            end
            host_clock = 0;
        end
    endtask
    task automatic write_word(input [31:0] a, input [31:0] w);
        integer before_count;
        begin
            expected_address = a & 32'hffff_fffc;
            expected_write = endian_little ? swapped(w) : w;
            expect_write = 1;
            before_count = writes;
            begin_frame();
            send_pairs(expected_address | 1, 16);
            send_pairs(w, 16);
            // Final byte and strobe traverse the APF synchronizer/pipeline.
            #(16 * 2 * CORE_HALF_PS);
            if (writes != before_count+1) $fatal(1, "BRIDGE_WRITE missing/duplicate transaction");
            end_frame();
        end
    endtask
    task automatic read_word(input [31:0] a);
        integer k, before_count;
        reg [31:0] got, want;
        reg [1:0] sampled;
        begin
            expected_address = a & 32'hffff_fffc;
            before_count = reads;
            want = endian_little ? swapped(buffered_expected) : buffered_expected;
            expect_read = 1;
            begin_frame();
            send_pairs(expected_address, 16);
            if (!CONTENTION) begin
                host_drive = 0;
                wait (return_clock === 1'bz);
                wait (return_clock === 1'b1);
            end
            // APF first drives clock/data high, then sends 16 two-bit pairs.
            // Ignore Z->1 turnaround and the initial high preamble.
            got = 0;
            for (k=0; k<16; k=k+1) begin
                @(negedge return_clock);
                if (return_clock !== 1'b0) $fatal(1, "BRIDGE_READ invalid falling clock");
                @(posedge return_clock);
                if (return_clock !== 1'b1 || $isunknown(return_data))
                    $fatal(1, "BRIDGE_READ invalid sampled pair");
                sampled = return_data;
                if ($time-last_return_change < min_return_setup) min_return_setup = $time-last_return_change;
                if (CHECK_WINDOWS && $time-last_return_change < SETUP_PS)
                    $fatal(1, "BRIDGE_RETURN_WINDOW setup");
                got = {got[29:0], sampled};
                tx_pairs = tx_pairs + 1;
                last_sample = $time;
                waiting_hold = 1;
                #HOLD_PS;
                if (CHECK_WINDOWS && ($time-last_return_change < HOLD_PS || return_data !== sampled))
                    $fatal(1, "BRIDGE_RETURN_WINDOW hold");
            end
            if (got !== want) $fatal(1, "BRIDGE_READ got=%h expected=%h", got, want);
            #(12 * 2 * CORE_HALF_PS);
            if (reads != before_count+1 || pending_response >= 0 || read_data !== response(expected_address))
                $fatal(1, "BRIDGE_RESPONSE request/late response");
            buffered_expected = response(expected_address);
            end_frame();
        end
    endtask
    task automatic abort_frame(input integer after_address);
        integer before_writes, before_reads;
        begin
            before_writes = writes;
            before_reads = reads;
            begin_frame();
            if (after_address) begin
                send_pairs(32'h1234_0001, 16);
                send_pairs(32'h1357_2468, 8);
            end else send_pairs(32'h1234_0001, 8);
            end_frame();
            if (writes != before_writes || reads != before_reads)
                $fatal(1, "BRIDGE_ABORT partial packet issued a transaction");
            aborts = aborts + 1;
        end
    endtask

    integer endian, i;
    reg [31:0] a, w;
    initial begin
        #(16 * 2 * CORE_HALF_PS);
        reset_n = 1;
        active = 1;
        #PHASE_PS;
        abort_frame(0);
        abort_frame(1);
        for (endian=0; endian<2; endian=endian+1) begin
            endian_little = endian;
            #(12 * 2 * CORE_HALF_PS);
            for (i=0; i<8; i=i+1) begin
                a = 32'h2000_0000 ^ (32'h3915_2384 * (i+1));
                w = 32'h1357_9bdf ^ (32'h3141_5927 * (i+1));
                write_word(a, w);
                read_word(a ^ 32'h0842_1000);
            end
        end
        if (writes != 16 || reads != 16 || aborts != 2 || rx_edges != 800 || tx_pairs != 256 || min_return_hold == 64'h7fff_ffff_ffff_ffff)
            $fatal(1, "BRIDGE_COVERAGE missing operations/edges");
        $display("PASS APF_BRIDGE writes=%0d reads=%0d aborts=%0d rx_edges=%0d tx_pairs=%0d min_input_setup_ps=%0d min_return_setup_ps=%0d min_return_hold_ps=%0d", writes,reads,aborts,rx_edges,tx_pairs,min_input_setup,min_return_setup,min_return_hold);
        $finish;
    end
    initial begin
        #100000000;
        $fatal(1, "BRIDGE_TIMEOUT no complete transfer");
    end
endmodule
