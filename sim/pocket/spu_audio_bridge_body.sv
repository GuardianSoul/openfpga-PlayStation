// SPDX-License-Identifier: MIT
// Inserted into the actual SPU completion fixture, not a regular sample source.
reg bridge_audio_clk=0, bridge_running=0;
always #40690104 bridge_audio_clk=~bridge_audio_clk;
always @(posedge clk1x) bridge_running<=ce && !reset;
wire bridge_reset=reset || SS_reset;
wire bridge_full, bridge_fault, bridge_tick;
wire [31:0] bridge_output;
wire [6:0] bridge_level;
psx_audio_bridge #(.COEFFICIENT_FILE(`COEFFICIENT_FILE)) audio_bridge (
    .src_clk(clk1x),.audio_clk(bridge_audio_clk),.reset_async(bridge_reset),
    .src_running(bridge_running),.src_valid(audio_pair_valid),
    .src_frame({sound_out_right_candidate,sound_out_left_candidate}),
    .src_full(bridge_full),.src_overflow(),.frame_tick(bridge_tick),
    .output_frame(bridge_output),.buffered_frames(bridge_level),
    .fault(bridge_fault),.rate_correction(),.audio_lrck(),.audio_dac());
reg [31:0] bridge_queue[0:16383];
integer bridge_written=0,bridge_read=0,bridge_packets=0,bridge_outputs=0;
integer bridge_nonzero_source=0,bridge_nonzero_output=0;
integer bridge_last_pair=-1,bridge_min_gap=2147483647,bridge_max_gap=0;
integer bridge_max_level=0;
always @(posedge clk1x) begin
    if(bridge_reset) begin
        bridge_written=0;
        bridge_last_pair=-1;
    end else if(audio_pair_valid && !audio_bridge.src_reset) begin
        if(bridge_full) $fatal(1,"Actual SPU overflows audio FIFO");
        if(bridge_written>=16384) $fatal(1,"SPU bridge test queue capacity");
        bridge_queue[bridge_written]={sound_out_right_candidate,sound_out_left_candidate};
        bridge_written++;
        if({sound_out_right_candidate,sound_out_left_candidate}!=0) bridge_nonzero_source++;
        if(bridge_last_pair>=0) begin
            if(cycles-bridge_last_pair<bridge_min_gap) bridge_min_gap=cycles-bridge_last_pair;
            if(cycles-bridge_last_pair>bridge_max_gap) bridge_max_gap=cycles-bridge_last_pair;
        end
        bridge_last_pair=cycles;
    end
end
always @(posedge bridge_audio_clk) begin
    if(bridge_reset) bridge_read=0;
    else if(!audio_bridge.audio_reset) begin
        if(audio_bridge.popped_valid) begin
            if(bridge_read>=bridge_written || audio_bridge.popped_frame!==bridge_queue[bridge_read])
                $fatal(1,"SPU bridge FIFO order pair=%0d",bridge_read);
            bridge_read++;
            bridge_packets++;
        end
    end
end
always @(negedge bridge_audio_clk) if(!audio_bridge.audio_reset) begin
    if(bridge_fault!==1'b0)
        $fatal(1,"Actual SPU audio bridge fault overflow=%b underflow=%b deadline=%b level=%0d",
               audio_bridge.src_overflow,audio_bridge.underflow,audio_bridge.missed_deadline,bridge_level);
    if(bridge_level>bridge_max_level) bridge_max_level=bridge_level;
    if(bridge_tick) begin
        bridge_outputs++;
        if(bridge_output!=0) bridge_nonzero_output++;
    end
end
task check_audio_bridge;
    begin
        if(bridge_packets<1000 || bridge_nonzero_source<100 || bridge_nonzero_output<100)
            $fatal(1,"Insufficient actual SPU bridge audio coverage packets=%0d source=%0d output=%0d",
                   bridge_packets,bridge_nonzero_source,bridge_nonzero_output);
        if(bridge_max_gap<50000 || bridge_min_gap>=768)
            $fatal(1,"SPU completion jitter/pause coverage min=%0d max=%0d",bridge_min_gap,bridge_max_gap);
        $display("PASS actual SPU audio bridge: packets=%0d ticks=%0d nonzero_source=%0d nonzero_output=%0d gap_min=%0d gap_max=%0d max_level=%0d",
                 bridge_packets,bridge_outputs,bridge_nonzero_source,bridge_nonzero_output,
                 bridge_min_gap,bridge_max_gap,bridge_max_level);
    end
endtask
