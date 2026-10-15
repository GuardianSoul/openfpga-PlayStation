`timescale 1ns/1ps
module tb_audio_sinc;
    reg clk = 0;
    always #5 clk = ~clk;
    reg reset = 1, append_valid = 0, start = 0;
    reg [31:0] append_frame = 0;
    reg [7:0] phase_coarse = 0;
    reg [15:0] phase_fine = 0;
    wire ready, busy, result_valid;
    wire [31:0] result_frame;
    psx_audio_sinc #(.COEFFICIENT_FILE(`COEFFICIENT_FILE)) dut (.*);
    reg [8191:0] vector_path;
    integer fd, count, command, a, b, wanted, fields, clocks, worst = 0;
    initial begin
        if (!$value$plusargs("VECTORS=%s", vector_path)) $fatal(1, "VECTORS missing");
        fd = $fopen(vector_path, "r");
        if (!fd) $fatal(1, "Vector file open");
        repeat (3) @(negedge clk);
        reset = 0;
        wait (ready); @(negedge clk);
        count = 0;
        while (!$feof(fd)) begin
            fields = $fscanf(fd, "%d %h %h %h\n", command, a, b, wanted);
            if (fields != 4) $fatal(1, "Vector fields");
            if (command == 0) begin
                append_frame = a; append_valid = 1;
                @(negedge clk); append_valid = 0;
            end else if (command == 1) begin
                phase_coarse = a; phase_fine = b; start = 1;
                @(negedge clk); start = 0;
                clocks = 0;
                while (!result_valid && clocks < 256) begin
                    @(negedge clk); clocks = clocks + 1;
                end
                if (!result_valid || result_frame !== wanted || !ready || busy)
                    $fatal(1, "Audio result %0d phase %0d/%0d got %h wanted %h in %0d clocks", count,a,b,result_frame,wanted,clocks);
                if (clocks > worst) worst = clocks;
                count = count + 1;
                @(negedge clk);
                if (result_valid) $fatal(1, "Duplicate audio result");
            end else if (command == 2) begin
                reset = 1; repeat (3) @(negedge clk); reset = 0;
                wait (ready); @(negedge clk);
            end else $fatal(1, "Unknown vector command");
        end
        $display("PASS audio sinc: %0d stereo results, worst %0d clocks", count, worst);
        $finish;
    end
    initial begin #100000000; $fatal(1, "Audio FIR timeout"); end
endmodule
