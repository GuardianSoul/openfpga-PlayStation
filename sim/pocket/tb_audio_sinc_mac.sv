// psx_audio_sinc_mac.sv (as psx_audio_sinc) against the original converter
// (renamed psx_audio_sinc_ref) with the same coefficients: random frames
// appended and random phases started within the converter's contract,
// resets in between; every output is compared on every clock.
`timescale 1ns/1ps
module tb_audio_sinc_mac #(parameter integer SEED = 1, parameter integer CLOCKS = 3000000);
    reg clk = 0; always #5 clk = ~clk;
    reg reset = 1, append_valid = 0, start = 0;
    reg [31:0] append_frame = 0; reg [7:0] phase_coarse = 0; reg [15:0] phase_fine = 0;
    wire r0, r1, b0, b1, v0, v1; wire [31:0] q0, q1;
    psx_audio_sinc_ref #(.COEFFICIENT_FILE(`COEFFICIENT_FILE)) ref_ (.clk(clk), .reset(reset),
        .append_valid(append_valid), .append_frame(append_frame), .start(start), .phase_coarse(phase_coarse),
        .phase_fine(phase_fine), .ready(r0), .busy(b0), .result_valid(v0), .result_frame(q0));
    psx_audio_sinc #(.COEFFICIENT_FILE(`COEFFICIENT_FILE)) dut (.clk(clk), .reset(reset),
        .append_valid(append_valid), .append_frame(append_frame), .start(start), .phase_coarse(phase_coarse),
        .phase_fine(phase_fine), .ready(r1), .busy(b1), .result_valid(v1), .result_frame(q1));
    integer i, s = SEED, errors = 0, results = 0, mode = 0, k;
    initial begin
        for (i = 0; i < CLOCKS; i = i + 1) begin
            @(negedge clk);
            if ({r0, b0, v0, q0} !== {r1, b1, v1, q1}) begin
                errors = errors + 1;
                if (errors <= 10) $display("ERROR clock %0d ref %b%b%b %h dut %b%b%b %h", i, r0, b0, v0, q0, r1, b1, v1, q1);
            end
            if (v0) results = results + 1;
            append_valid = 0; start = 0;
            if (i % 100000 == 0) mode = $random(s) & 3;
            reset = i < 3 || ($random(s) % 400000) == 0;
            if (r0 && !reset) begin
                k = $random(s) & 7;
                if (k < 3) begin
                    append_valid = 1;
                    case (mode)   // full-scale noise, extremes, small values, a slow ramp
                        0: append_frame = $random(s);
                        1: append_frame = {($random(s) & 1) ? 16'h8000 : 16'h7fff, ($random(s) & 1) ? 16'h8000 : 16'h7fff};
                        2: append_frame = {16'($signed($random(s)) >>> 24), 16'($signed($random(s)) >>> 24)};
                        default: append_frame = {16'(i * 7), 16'(-i * 5)};
                    endcase
                end else if (k < 6) begin
                    start = 1; phase_coarse = $unsigned($random(s)) % 160;
                    phase_fine = (($random(s) & 3) == 0) ? (($random(s) & 1) ? 16'hffff : 16'h0000) : $random(s);
                end
            end
        end
        $display("RESULT errors %0d clocks %0d results %0d", errors, CLOCKS, results);
        $finish;
    end
endmodule
