/*
 * Copyright (c) 2012, Stefan Kristiansson <stefan.kristiansson@saunalahti.fi>
 * All rights reserved.
 *
 * Based on vga_fifo_dc.v in Richard Herveille's VGA/LCD core
 * Copyright (C) 2001 Richard Herveille <richard@asics.ws>
 *
 * Redistribution and use in source and non-source forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *     * Redistributions of source code must retain the above copyright
 *       notice, this list of conditions and the following disclaimer.
 *     * Redistributions in non-source form must reproduce the above copyright
 *       notice, this list of conditions and the following disclaimer in the
 *       documentation and/or other materials provided with the distribution.
 *
 * THIS WORK IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
 * THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
 * PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
 * CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
 * EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
 * PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * WORK, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

// FIFO protocol adapted from the pinned Laxer GPU FIFO.
// Atomic SPU stereo packets -> properly filtered 48 kHz Pocket audio stream.
// reset_async must be a glitch-free registered system/PLL reset. Both local
// resets assert asynchronously and release after three local clock edges.
// FIFO is a Gray-pointer dual-clock ring (same method as pinned Laxer FIFO),
// with explicit reset of both pointer synchronizers. No change detection:
// repeated zero/equal samples are real frames and must traverse the FIFO.
module psx_audio_bridge #(
    parameter COEFFICIENT_FILE = ""
) (
    input wire src_clk, audio_clk, reset_async,
    input wire src_running,
    input wire src_valid,
    input wire [31:0] src_frame, // completed coherent {right,left}
    output wire src_full,
    output reg src_overflow = 0,
    output reg frame_tick = 0,
    output reg [31:0] output_frame = 0,
    output wire [6:0] buffered_frames,
    output wire fault,
    // Independent diagnostic bits, all in audio_clk: bit0 fatal, bit1 source
    // overflow, bit2 starvation seen, bit3 missed deadline, bit4 not empty,
    // bit5 FIR ready, bit6 active, bit7 synchronized source running.
    output wire [7:0] status,
    output wire signed [15:0] rate_correction,
    output wire audio_lrck, audio_dac
);
    (* async_reg="true", preserve, dont_merge *) reg [2:0] src_reset_pipe=7, audio_reset_pipe=7;
    always @(posedge src_clk or posedge reset_async)
        if (reset_async) src_reset_pipe<=7; else src_reset_pipe<={src_reset_pipe[1:0],1'b0};
    always @(posedge audio_clk or posedge reset_async)
        if (reset_async) audio_reset_pipe<=7; else audio_reset_pipe<={audio_reset_pipe[1:0],1'b0};
    wire src_reset=src_reset_pipe[2], audio_reset=audio_reset_pipe[2];
    psx_audio_i2s serializer (.audio_clk(audio_clk),.reset(audio_reset),
        .frame(output_frame),.audio_lrck(audio_lrck),.audio_dac(audio_dac));

    (* ramstyle="M10K" *) reg [31:0] packets[0:63];
    reg [6:0] wr_bin=0, wr_gray=0, rd_bin=0, rd_gray=0;
    (* async_reg="true", preserve, dont_merge *) reg [6:0] rd_gray_meta=0, rd_gray_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg [6:0] wr_gray_meta=0, wr_gray_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg running_meta=0, running_sync=0;
    (* async_reg="true", preserve, dont_merge *) reg overflow_meta=0, overflow_sync=0;
    function automatic [6:0] gray_to_binary(input [6:0] value);
        integer i;
        begin
            gray_to_binary[6]=value[6];
            for(i=5;i>=0;i=i-1) gray_to_binary[i]=gray_to_binary[i+1]^value[i];
        end
    endfunction
    wire [6:0] wr_next=wr_bin+7'd1, rd_next=rd_bin+7'd1;
    assign src_full=wr_gray=={~rd_gray_sync[6:5],rd_gray_sync[4:0]};
    wire empty=rd_gray==wr_gray_sync;
    assign buffered_frames=gray_to_binary(wr_gray_sync)-rd_bin;
    reg pop=0, popped_valid=0;
    reg [31:0] popped_frame=0;
    always @(posedge src_clk) if(src_valid && !src_full && !src_reset)
        packets[wr_bin[5:0]]<=src_frame;
    always @(posedge src_clk or posedge src_reset) begin
        if(src_reset) begin
            wr_bin<=0; wr_gray<=0; rd_gray_meta<=0; rd_gray_sync<=0; src_overflow<=0;
        end else begin
            rd_gray_meta<=rd_gray; rd_gray_sync<=rd_gray_meta;
            if(src_valid) begin
                if(src_full) src_overflow<=1;
                else begin wr_bin<=wr_next; wr_gray<=(wr_next>>1)^wr_next; end
            end
        end
    end
    always @(posedge audio_clk) if(pop && !empty && !audio_reset)
        popped_frame<=packets[rd_bin[5:0]];
    always @(posedge audio_clk or posedge audio_reset) begin
        if(audio_reset) begin
            rd_bin<=0; rd_gray<=0; wr_gray_meta<=0; wr_gray_sync<=0;
            running_meta<=0; running_sync<=0; overflow_meta<=0; overflow_sync<=0;
            popped_valid<=0;
        end else begin
            wr_gray_meta<=wr_gray; wr_gray_sync<=wr_gray_meta;
            running_meta<=src_running; running_sync<=running_meta;
            overflow_meta<=src_overflow; overflow_sync<=overflow_meta;
            popped_valid<=pop && !empty;
            if(pop && !empty) begin rd_bin<=rd_next; rd_gray<=(rd_next>>1)^rd_next; end
        end
    end

    wire fir_ready, fir_busy, fir_valid;
    wire [31:0] fir_frame;
    reg append_valid=0, start=0, advance=0;
    reg [31:0] append_frame=0;
    wire [7:0] phase_coarse;
    wire [15:0] phase_fine;
    wire need_input;
    psx_audio_rate rate (.clk(audio_clk),.reset(audio_reset),.advance(advance),
        .level(buffered_frames),.phase_coarse(phase_coarse),.phase_fine(phase_fine),
        .need_input(need_input),.correction(rate_correction));
    psx_audio_sinc #(.COEFFICIENT_FILE(COEFFICIENT_FILE)) converter (
        .clk(audio_clk),.reset(audio_reset),.append_valid(append_valid),.append_frame(append_frame),
        .start(start),.phase_coarse(phase_coarse),.phase_fine(phase_fine),.ready(fir_ready),
        .busy(fir_busy),.result_valid(fir_valid),.result_frame(fir_frame));
    localparam IDLE=0, WAIT_POP=1, APPEND=2, WAIT_FIR=3;
    reg [1:0] state=IDLE;
    reg [7:0] frame_count=0;
    reg active=0, pending_valid=0;
    reg [31:0] pending_frame=0;
    reg underflow=0, missed_deadline=0;
    assign fault=overflow_sync || underflow || missed_deadline;
    wire fatal_fault=overflow_sync || missed_deadline;
    assign status={running_sync,active,fir_ready,!empty,missed_deadline,underflow,overflow_sync,fatal_fault};
    always @(posedge audio_clk) begin
        frame_tick<=0; pop<=0; append_valid<=0; start<=0; advance<=0;
        if(audio_reset) begin
            frame_count<=0; state<=IDLE; active<=0; pending_valid<=0;
            pending_frame<=0; output_frame<=0; underflow<=0; missed_deadline<=0;
        end else begin
            frame_count<=frame_count+8'd1;
            if(&frame_count) begin
                frame_tick<=1;
                output_frame<=0;
                if(running_sync && !fatal_fault) begin
                    if(pending_valid) begin output_frame<=pending_frame; pending_valid<=0; end
                    if(state!=IDLE) missed_deadline<=1;
                    else if(fir_ready && (active || buffered_frames>=32)) begin
                        active<=1;
                        if(need_input) begin
                            // Starvation loses no queued source packet. Pause
                            // the converter until its reserve refills; retain
                            // phase/history/order and the diagnostic evidence.
                            // Overflow/deadline faults still require reset.
                            if(empty) begin underflow<=1;active<=0;end
                            else begin pop<=1; state<=WAIT_POP; end
                        end else begin start<=1; state<=WAIT_FIR; end
                    end
                end
            end
            case(state)
                WAIT_POP: if(popped_valid) begin
                    append_frame<=popped_frame; append_valid<=1; state<=APPEND;
                end
                APPEND: begin start<=1; state<=WAIT_FIR; end
                WAIT_FIR: if(fir_valid) begin
                    pending_frame<=fir_frame; pending_valid<=1; advance<=1; state<=IDLE;
                end
                default: ;
            endcase
        end
    end
endmodule
