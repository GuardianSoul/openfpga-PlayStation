`timescale 1ns/1ps
`default_nettype none

module tb_psx_mem_profile;
    parameter integer PORTS = 4;
    parameter integer COUNTER_WIDTH = 16;
    localparam [63:0] LIMIT = (64'd1 << COUNTER_WIDTH) - 1;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1, clear = 0;
    reg [PORTS-1:0] issue = 0, waiting = 0;
    reg [7:0] issue_bytes = 0;
    reg complete = 0;
    wire busy;
    wire [COUNTER_WIDTH-1:0] cycles, busy_cycles, protocol_errors;
    wire [PORTS-1:0][COUNTER_WIDTH-1:0] requests, completions, bytes_issued;
    wire [PORTS-1:0][COUNTER_WIDTH-1:0] wait_cycles, latency_sum, latency_max;

    psx_mem_profile #(.PORTS(PORTS), .COUNTER_WIDTH(COUNTER_WIDTH)) dut (.*);

    // The oracle records issue timestamps and unbounded integer totals; it
    // does not reproduce the DUT's age or saturating-adder implementation.
    integer epoch, started, owner, expected_busy;
    integer active_cycles, errors, req [PORTS], done [PORTS], bytes [PORTS];
    integer waits [PORTS], sums [PORTS], maxima [PORTS];
    integer checked = 0;
    reg [31:0] random_state = 32'h50535801;

    function automatic [COUNTER_WIDTH-1:0] clamp(input integer value);
        clamp = value > LIMIT ? LIMIT : value;
    endfunction

    task automatic reset_oracle;
        begin
            epoch = 0; started = 0; owner = 0; expected_busy = 0;
            active_cycles = 0; errors = 0;
            for (integer i = 0; i < PORTS; i = i + 1) begin
                req[i] = 0; done[i] = 0; bytes[i] = 0;
                waits[i] = 0; sums[i] = 0; maxima[i] = 0;
            end
        end
    endtask

    task automatic compare_outputs;
        begin
            if (busy !== expected_busy[0] || cycles !== COUNTER_WIDTH'(clamp(epoch)) ||
                busy_cycles !== COUNTER_WIDTH'(clamp(active_cycles)) ||
                protocol_errors !== COUNTER_WIDTH'(clamp(errors)))
                $fatal(1, "Global accounting mismatch at sample %0d: cycles=%0d expected=%0d busy=%0d expected=%0d errors=%0d expected=%0d",
                       checked, cycles, clamp(epoch), busy, expected_busy, protocol_errors, clamp(errors));
            for (integer i = 0; i < PORTS; i = i + 1) begin
                if (requests[i] !== COUNTER_WIDTH'(clamp(req[i])) ||
                    completions[i] !== COUNTER_WIDTH'(clamp(done[i])) ||
                    bytes_issued[i] !== COUNTER_WIDTH'(clamp(bytes[i])) ||
                    wait_cycles[i] !== COUNTER_WIDTH'(clamp(waits[i])) ||
                    latency_sum[i] !== COUNTER_WIDTH'(clamp(sums[i])) ||
                    latency_max[i] !== COUNTER_WIDTH'(clamp(maxima[i])))
                    $fatal(1, "Port %0d accounting mismatch at sample %0d", i, checked);
            end
        end
    endtask

    task automatic sample(
        input reg [PORTS-1:0] issued,
        input reg [7:0] byte_count,
        input reg finished,
        input reg [PORTS-1:0] stalled
    );
        integer n, selected, old_busy, elapsed;
        begin
            @(negedge clk);
            rst = 0; clear = 0;
            issue = issued; issue_bytes = byte_count;
            complete = finished; waiting = stalled;
            n = 0; selected = 0; old_busy = expected_busy;
            for (integer i = 0; i < PORTS; i = i + 1) begin
                if (issued[i]) begin n = n + 1; selected = i; end
                if (stalled[i]) waits[i] = waits[i] + 1;
            end
            epoch = epoch + 1;
            if (old_busy) active_cycles = active_cycles + 1;
            if ((n > 1) || (n == 1 && old_busy && !finished) ||
                (finished && !old_busy)) errors = errors + 1;
            if (finished && old_busy) begin
                elapsed = epoch - started;
                done[owner] = done[owner] + 1;
                sums[owner] = sums[owner] + elapsed;
                if (elapsed > maxima[owner]) maxima[owner] = elapsed;
                expected_busy = 0;
            end
            if (n == 1 && (!old_busy || finished)) begin
                req[selected] = req[selected] + 1;
                bytes[selected] = bytes[selected] + byte_count;
                started = epoch; owner = selected; expected_busy = 1;
            end
            @(posedge clk); #1;
            checked = checked + 1;
            compare_outputs();
        end
    endtask

    task automatic clear_epoch;
        begin
            @(negedge clk); clear = 1; issue = 0; waiting = 0; complete = 0;
            reset_oracle();
            @(posedge clk); #1; compare_outputs();
        end
    endtask

    initial begin
        reset_oracle();
        repeat (2) @(posedge clk);
        #1; compare_outputs();
        sample(0, 0, 1, 0);                       // idle completion
        sample(1, 4, 0, '1);                      // first issue
        sample(0, 0, 0, '1);
        sample(1, 4, 0, '1);                      // illegal second issue
        sample(0, 0, 0, 0);
        sample(PORTS'(1 << (PORTS-1)), 16, 1, 0); // handoff on completion
        sample(0, 0, 1, 0);                       // one-cycle latency
        if (PORTS > 1) sample(PORTS'(3), 4, 0, 0);// simultaneous issues rejected
        sample(1, 255, 0, 0);                     // byte saturation, narrow counter
        repeat (40) sample(0, 0, 0, '1);          // age/wait saturation
        sample(1, 4, 1, 0);                       // completion/reissue same owner
        sample(0, 0, 1, 0);
        sample(1, 1, 0, 0);
        clear_epoch();                           // discard in-flight sample
        sample(0, 0, 1, 0);                       // late response is stray

        for (integer step = 0; step < 2000; step = step + 1) begin
            random_state = random_state * 32'd1664525 + 32'd1013904223;
            sample(PORTS'(random_state >> 8), random_state[23:16],
                   random_state[29], PORTS'(random_state >> 20));
        end
        // Synchronous reset also clears a live measurement epoch.
        @(negedge clk); rst = 1; reset_oracle();
        @(posedge clk); #1; compare_outputs();
        $display("PASS ports=%0d counter_width=%0d samples=%0d", PORTS, COUNTER_WIDTH, checked);
        $finish;
    end

    initial begin #1000000; $fatal(1, "Simulation watchdog expired"); end
endmodule
`default_nettype wire
