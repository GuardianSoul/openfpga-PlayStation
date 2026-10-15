// Pocket output-rate converter. Guest SPU arithmetic/timing is upstream.
// 128 source taps, 160 phases + endpoint, fine interpolation between phases.
// One stereo result uses 132 clocks; the 48 kHz sink supplies 256 clocks.
// Coefficients: scripts/generate_pocket_audio_coeffs.py, Q17 in 18 bits.
module psx_audio_sinc #(
    parameter COEFFICIENT_FILE = ""
) (
    input wire clk,
    input wire reset,
    input wire append_valid,
    input wire [31:0] append_frame, // {right,left}, signed 16-bit samples
    input wire start,
    input wire [7:0] phase_coarse,  // 0..159
    input wire [15:0] phase_fine,
    output wire ready,
    output reg busy = 1'b0,
    output reg result_valid = 1'b0,
    output reg [31:0] result_frame = 32'b0
);
    // Store phases 0..80. The remaining integer rows are exact mirrors.
    // Phase 80 itself has an asymmetric DC-rounding residual; its base read
    // uses the direct order while its next-phase read uses mirrored phase 79.
    (* ramstyle = "M10K" *) reg [17:0] coefficients [0:10367];
    (* ramstyle = "M10K" *) reg [31:0] history [0:127];
    initial $readmemh(COEFFICIENT_FILE, coefficients);

    reg clearing = 1'b1;
    reg [6:0] clear_index = 0;
    // Initially newest==127, so the first append goes into entry 0.
    reg [6:0] newest = 7'd127;
    reg [7:0] phase = 0;
    reg [15:0] fraction = 0;
    reg [7:0] issued = 0;
    wire issue = busy && issued < 8'd128;
    wire [7:0] phase_a = phase > 80 ? 8'd160 - phase : phase;
    wire [7:0] phase_b = phase >= 80 ? 8'd159 - phase : phase + 8'd1;
    wire [6:0] tap_a = phase > 80 ? ~issued[6:0] : issued[6:0];
    wire [6:0] tap_b = phase >= 80 ? ~issued[6:0] : issued[6:0];
    wire [13:0] address_a = {phase_a[6:0], tap_a};
    wire [13:0] address_b = {phase_b[6:0], tap_b};
    wire [6:0] history_address = newest - issued[6:0];
    assign ready = !clearing && !busy;

    reg [17:0] coefficient_a, coefficient_b;
    reg [31:0] history_1, history_2, history_3;
    reg valid_1 = 0, valid_2 = 0, valid_3 = 0, valid_4 = 0;
    reg last_1, last_2, last_3, last_4;
    reg signed [18:0] base_2, coefficient_3;
    reg signed [29:0] interpolation_2;
    reg signed [33:0] product_left, product_right;
    reg signed [40:0] sum_left = 0, sum_right = 0;

    // 0x20000 denotes +1.0, never -1.0. It is otherwise unused by this bank.
    function automatic signed [18:0] decode(input [17:0] value);
        decode = value == 18'h20000 ? 19'sd131072 : {value[17], value};
    endfunction
    wire signed [18:0] delta_full = decode(coefficient_b) - decode(coefficient_a);
    wire signed [12:0] delta = delta_full[12:0];
    wire signed [16:0] fraction_signed = {1'b0, fraction};
    wire signed [40:0] accumulated_left = sum_left + product_left;
    wire signed [40:0] accumulated_right = sum_right + product_right;
    function automatic [15:0] quantize(input signed [40:0] value);
        reg signed [40:0] rounded;
        begin
            rounded = (value + 41'sd65536) >>> 17;
            if (rounded > 41'sd32767) quantize = 16'h7fff;
            else if (rounded < -41'sd32768) quantize = 16'h8000;
            else quantize = rounded[15:0];
        end
    endfunction

    // RAM ports have no asynchronous reset; clearing writes one history word
    // each clock. Appending is forbidden while a result reads the ring.
    always @(posedge clk) begin
        if (clearing) history[clear_index] <= 0;
        else if (append_valid && ready) history[newest + 7'd1] <= append_frame;
        if (issue) begin
            coefficient_a <= coefficients[address_a];
            coefficient_b <= coefficients[address_b];
            history_1 <= history[history_address];
        end
    end

    always @(posedge clk) begin
        result_valid <= 0;
        if (reset) begin
            clearing <= 1;
            clear_index <= 0;
            newest <= 7'd127;
            busy <= 0;
            issued <= 0;
            valid_1 <= 0;
            valid_2 <= 0;
            valid_3 <= 0;
            valid_4 <= 0;
            sum_left <= 0;
            sum_right <= 0;
            result_frame <= 0;
        end else if (clearing) begin
            if (clear_index == 7'd127) clearing <= 0;
            else clear_index <= clear_index + 7'd1;
        end else begin
            if (append_valid && ready) newest <= newest + 7'd1;
            if (start && ready && !append_valid) begin
                busy <= 1;
                phase <= phase_coarse;
                fraction <= phase_fine;
                issued <= 0;
                sum_left <= 0;
                sum_right <= 0;
            end
            if (issue) issued <= issued + 8'd1;
            valid_1 <= issue;
            last_1 <= issued == 8'd127;
            valid_2 <= valid_1;
            last_2 <= last_1;
            valid_3 <= valid_2;
            last_3 <= last_2;
            valid_4 <= valid_3;
            last_4 <= last_3;
            if (valid_1) begin
                base_2 <= decode(coefficient_a);
                interpolation_2 <= delta * fraction_signed;
                history_2 <= history_1;
            end
            if (valid_2) begin
                coefficient_3 <= base_2 + ((interpolation_2 + 30'sd32768) >>> 16);
                history_3 <= history_2;
            end
            if (valid_3) begin
                // The unity case needs no multiplier and lets the regular
                // product use an 18 x 16 signed DSP datapath.
                if (coefficient_3 == 19'sd131072) begin
                    product_left <= $signed(history_3[15:0]) * 34'sd131072;
                    product_right <= $signed(history_3[31:16]) * 34'sd131072;
                end else begin
                    product_left <= $signed(history_3[15:0]) * $signed(coefficient_3[17:0]);
                    product_right <= $signed(history_3[31:16]) * $signed(coefficient_3[17:0]);
                end
            end
            if (valid_4) begin
                sum_left <= accumulated_left;
                sum_right <= accumulated_right;
                if (last_4) begin
                    result_frame <= {quantize(accumulated_right), quantize(accumulated_left)};
                    result_valid <= 1;
                    busy <= 0;
                end
            end
        end
    end
`ifndef SYNTHESIS
    always @(posedge clk) if (!reset && !clearing) begin
        if (append_valid && !ready) $fatal(1, "Audio history append while busy");
        if (start && (!ready || append_valid || phase_coarse >= 160))
            $fatal(1, "Audio FIR start contract");
        if (valid_1 && delta_full != {{6{delta[12]}}, delta})
            $fatal(1, "Audio coefficient delta overflow");
    end
`endif
endmodule
