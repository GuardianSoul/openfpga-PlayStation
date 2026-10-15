// MIT License
// Copyright (c) 2022 Adam Gastineau
// Copyright (c) 2026 PSX Pocket contributors
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
//
// Adapted from pinned MIRLO64/target/pocket/sound_i2s.sv. Same signed 16-bit
// framing, shifter reload and one-bit I2S delay, now with initialized reset and
// MCLK clock enable instead of a second internal clock domain.
module psx_audio_i2s (
    input wire audio_clk, reset,
    input wire [31:0] frame, // {right,left}, stable in audio_clk domain
    output reg audio_lrck=0,
    output reg audio_dac=0
);
    reg [1:0] divider=0;
    reg [4:0] bit_count=0;
    reg [31:0] shifter=0;
    always @(posedge audio_clk) begin
        if(reset) begin
            divider<=0; bit_count<=0; shifter<=0;
            audio_lrck<=0; audio_dac<=0;
        end else begin
            divider<=divider+2'd1;
            if(divider==2'd1) begin
                audio_dac<=shifter[31];
                bit_count<=bit_count+5'd1;
                if(bit_count==5'd31) begin
                    audio_lrck<=~audio_lrck;
                    if(!audio_lrck) shifter<=frame;
                end else if(bit_count<5'd16) shifter<={shifter[30:0],1'b0};
            end
        end
    end
endmodule
