// psx_audio_rate_lean.sv (as psx_audio_rate) against the original servo
// (renamed psx_audio_rate_ref): random FIFO levels, advance pulses and
// resets; every output is compared on every clock. Reports the count of
// checked clocks, wraps, clamped and other nonzero corrections.
`timescale 1ns/1ps
module tb_audio_rate_lean #(parameter integer SEED = 1, parameter integer CLOCKS = 4000000);
    reg clk = 0; always #5 clk = ~clk;
    reg reset = 1, advance = 0; reg [6:0] level = 32;
    wire [7:0] c0, c1; wire [15:0] f0, f1; wire n0, n1; wire signed [15:0] k0, k1;
    psx_audio_rate_ref ref_ (.clk(clk), .reset(reset), .advance(advance), .level(level),
        .phase_coarse(c0), .phase_fine(f0), .need_input(n0), .correction(k0));
    psx_audio_rate dut (.clk(clk), .reset(reset), .advance(advance), .level(level),
        .phase_coarse(c1), .phase_fine(f1), .need_input(n1), .correction(k1));
    integer i, s = SEED, errors = 0, wraps = 0, clamps = 0, mid = 0, mode = 0;
    initial begin
        for (i = 0; i < CLOCKS; i = i + 1) begin
            @(negedge clk);
            if ({c0, f0, n0, k0} !== {c1, f1, n1, k1}) begin
                errors = errors + 1;
                if (errors <= 10) $display("ERROR clock %0d ref %h %h %b %0d dut %h %h %b %0d", i, c0, f0, n0, k0, c1, f1, n1, k1);
            end
            if (n0 && advance) wraps = wraps + 1;
            if (k0 == 4933 || k0 == -4933) clamps = clamps + 1;
            else if (k0 != 0) mid = mid + 1;
            // phases: a slow random walk around 32, sweeps of every level, extremes
            if (i % 200000 == 0) mode = $random(s) & 3;
            case (mode)
                0: if (($random(s) & 63) == 0) level = level + (($random(s) & 1) ? 7'd1 : -7'd1);
                1: level = $random(s);
                2: level = (i / 3000) % 128;
                default: level = ($random(s) & 1) ? 7'd0 : 7'd127;
            endcase
            advance = ($random(s) & 3) != 0;
            reset = ($random(s) % 100000) == 0 || i < 3;
        end
        $display("RESULT errors %0d clocks %0d wraps %0d clamped %0d between %0d", errors, CLOCKS, wraps, clamps, mid);
        $finish;
    end
endmodule
