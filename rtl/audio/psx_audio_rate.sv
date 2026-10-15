// Rational 147/160 nominal rate with bounded FIFO-occupancy feedback.
// Fraction units are 1/(160*65536) source samples. Nominal rate is exact;
// independent oscillator drift is corrected without dropping input frames.
module psx_audio_rate (
    input wire clk, reset, advance,
    input wire [6:0] level,
    output wire [7:0] phase_coarse,
    output wire [15:0] phase_fine,
    output reg need_input = 1'b1,
    output reg signed [15:0] correction = 0
);
    reg [23:0] position = 0;
    reg signed [15:0] integral = 0;
    reg [7:0] servo_count = 0;
    wire signed [8:0] raw_error = $signed({1'b0,level}) - 9'sd32;
    // A two-frame dead band avoids correcting normal FIFO/clock jitter.
    wire signed [8:0] error = raw_error > 2 ? raw_error-9'sd2 :
                              raw_error < -2 ? raw_error+9'sd2 : 9'sd0;
    wire signed [18:0] proportional = $signed(error) * 19'sd512;
    wire signed [18:0] control = proportional + integral;
    function automatic signed [15:0] bounded(input signed [18:0] value);
        if (value > 19'sd4933) bounded = 16'sd4933;
        else if (value < -19'sd4933) bounded = -16'sd4933;
        else bounded = value[15:0];
    endfunction
    wire signed [15:0] next_correction = bounded(control);
    wire [24:0] next_position = {1'b0,position} + 25'd9633792 +
                                {{9{next_correction[15]}},next_correction};
    assign phase_coarse = position[23:16];
    assign phase_fine = position[15:0];
    always @(posedge clk) begin
        if (reset) begin
            position <= 0;
            need_input <= 1;
            integral <= 0;
            correction <= 0;
            servo_count <= 0;
        end else if (advance) begin
            need_input <= next_position >= 25'd10485760;
            position <= next_position >= 25'd10485760 ? next_position-25'd10485760 : next_position;
            correction <= next_correction;
            servo_count <= servo_count + 8'd1;
            if (&servo_count) integral <= bounded($signed(integral) + $signed(error));
        end
    end
endmodule
