`timescale 1fs/1fs
module tb_audio_bridge;
    reg src_clk=0, audio_clk=0, src_clock_run=1, audio_clock_run=1;
    always #14762550 if(src_clock_run) src_clk=~src_clk;
    always #40690104 if(audio_clock_run) audio_clk=~audio_clk;
    reg reset_async=1, src_running=0, src_valid=0;
    reg [31:0] src_frame=0;
    wire src_full, src_overflow, frame_tick, fault, audio_lrck, audio_dac;
    wire [31:0] output_frame;
    wire [6:0] buffered_frames;
    wire [7:0] status;
    wire signed [15:0] rate_correction;
    psx_audio_bridge #(.COEFFICIENT_FILE(`COEFFICIENT_FILE)) dut(.*);
    reg [17:0] coefficients[0:20607];
    reg [31:0] queue[0:100000], results[0:100000];
    integer qwr=0,qrd=0,rwr=0,rrd=0,source_count=0,sample_count=0;
    integer left_history[0:127],right_history[0:127];
    integer i, tap, base, following, coefficient, left_value, right_value;
    integer output_count=0,total_results=0,total_packets=0,pauses=0;
    integer paused_phase;
    reg allow_overflow=0,allow_starvation=0,allow_deadline=0,starve=0;
    integer recovery_goal,recovery_deadline,recovery_phase,recoveries=0;
    integer source_period=768,active_starts=0,slow_starts;
    reg last_active=0;
    reg [31:0] expected=0,expected_emit=0,last_packet=0;
    reg emit=0;
    longint signed sum_left,sum_right,delta,value;
    function integer decode(input [17:0] encoded);
        if(encoded==18'h20000) decode=131072;
        else if(encoded[17]) decode=$signed({14'h3fff,encoded});
        else decode=encoded;
    endfunction
    function integer quantize(input longint signed v);
        longint signed rounded;
        begin rounded=(v+65536)>>>17;
            quantize=rounded>32767 ? 32767 : rounded < -32768 ? -32768 : rounded;
        end
    endfunction
    // Source emits equal/zero/repeated frames too; every frame is queued.
    always @(negedge src_clk) begin
        src_valid=0;
        if(!dut.src_reset && src_running && !starve) begin
            if(allow_overflow || source_count>=source_period-1) begin
                source_count=0; src_valid=1;
                if(sample_count%7==0) src_frame=0;
                else begin
                    src_frame[15:0]=16'hab53^(sample_count*17);
                    src_frame[31:16]=16'h3571^(sample_count*29);
                end
                sample_count=sample_count+1;
            end else source_count=source_count+1;
        end
    end
    always @(posedge src_clk) begin
        if(src_valid && !dut.src_reset) begin
            if(src_full && !allow_overflow) $fatal(1,"Unexpected audio overflow");
            if(!src_full) begin queue[qwr]=src_frame; qwr=qwr+1; end
        end
    end
    always @(posedge audio_clk) begin
        emit=0;
        if(!dut.audio_reset) begin
            if(dut.popped_valid) begin
                if(qrd>=qwr || dut.popped_frame !== queue[qrd])
                    $fatal(1,"Stereo FIFO order %0d got %h expected %h",qrd,dut.popped_frame,queue[qrd]);
                last_packet=queue[qrd]; qrd=qrd+1; total_packets=total_packets+1;
            end
            if(dut.append_valid) begin
                if(dut.append_frame !== last_packet) $fatal(1,"FIR packet append");
                for(i=127;i>0;i=i-1) begin left_history[i]=left_history[i-1]; right_history[i]=right_history[i-1]; end
                left_history[0]=$signed(last_packet[15:0]); right_history[0]=$signed(last_packet[31:16]);
            end
            if(dut.start) begin
                sum_left=0; sum_right=0;
                for(tap=0;tap<128;tap=tap+1) begin
                    base=decode(coefficients[dut.phase_coarse*128+tap]);
                    following=decode(coefficients[(dut.phase_coarse+1)*128+tap]);
                    delta=following-base;
                    coefficient=base+((delta*dut.phase_fine+32768)>>>16);
                    value=coefficient; sum_left=sum_left+value*left_history[tap]; sum_right=sum_right+value*right_history[tap];
                end
                left_value=quantize(sum_left); right_value=quantize(sum_right);
                expected={right_value[15:0],left_value[15:0]};
            end
            if(dut.fir_valid) begin
                if(dut.fir_frame !== expected) $fatal(1,"Bridge FIR math got %h expected %h",dut.fir_frame,expected);
                results[rwr]=expected; rwr=rwr+1; total_results=total_results+1;
            end
            if(dut.frame_count==255) begin
                emit=1; expected_emit=0;
                if(dut.running_sync && !status[0] && dut.pending_valid) begin
                    if(rrd>=rwr) $fatal(1,"Audio emitted without result");
                    expected_emit=results[rrd]; rrd=rrd+1;
                end
            end
        end
    end
    always @(negedge audio_clk) begin
        if(dut.active && !last_active) active_starts=active_starts+1;
        last_active=dut.active;
        if(emit) begin
            if(!frame_tick || output_frame !== expected_emit) $fatal(1,"Audio sink order got %h expected %h",output_frame,expected_emit);
            output_count=output_count+1;
        end
        if(!allow_overflow && !allow_deadline && status[0]) $fatal(1,"Unexpected fatal audio bridge fault");
        if(!allow_starvation && status[2]) $fatal(1,"Unexpected audio starvation");
    end
    task begin_reset;
        begin
            reset_async=1; src_running=0; src_valid=0;
            qwr=0;qrd=0;rwr=0;rrd=0;source_count=0;sample_count=0;
            for(i=0;i<128;i=i+1) begin left_history[i]=0;right_history[i]=0; end
        end
    endtask
    task restart;
        begin
            begin_reset();
            repeat(8) @(negedge src_clk);
            reset_async=0; src_running=1;
        end
    endtask
    initial begin
        $readmemh(`FULL_COEFFICIENT_FILE,coefficients);
        restart();
        wait(output_count>=450);
        @(negedge src_clk); src_running=0;
        repeat(300) @(negedge audio_clk);
        wait(dut.state==0); @(negedge audio_clk);
        paused_phase={dut.phase_coarse,dut.phase_fine};
        repeat(10000) @(negedge audio_clk);
        if({dut.phase_coarse,dut.phase_fine}!==paused_phase || output_frame!==0) $fatal(1,"Paused audio phase/output changed");
        pauses=pauses+1;
        @(negedge src_clk); src_running=1;
        wait(output_count>=1050);
        // A real source can temporarily miss its nominal delivery rate while
        // still reporting running. Starve beyond the reserve without reset,
        // then resume: every source packet/FIR result still checks above.
        allow_starvation=1;
        repeat(2) begin
            @(negedge src_clk);starve=1;
            wait(!dut.active && status[2]);
            @(negedge audio_clk);
            if(status[0] || !fault) $fatal(1,"Starvation severity/evidence");
            recovery_phase={dut.phase_coarse,dut.phase_fine};
            repeat(4000) @(negedge audio_clk);
            if(output_frame!==0 || {dut.phase_coarse,dut.phase_fine}!==recovery_phase)
                $fatal(1,"Starved audio advanced converter history");
            recovery_goal=total_results+80;recovery_deadline=output_count+240;
            @(negedge src_clk);starve=0;
            while(total_results<recovery_goal && output_count<recovery_deadline) @(negedge audio_clk);
            if(total_results<recovery_goal || !dut.active || status[0] || !status[2])
                $fatal(1,"Audio underflow did not re-prime");
            recoveries++;
        end
        // Persistent25% undersupply must produce further re-prime intervals,
        // not a permanently wedged FIFO. No packet or filter step is skipped.
        slow_starts=active_starts;recovery_deadline=output_count+700;
        @(negedge src_clk);source_period=1024;
        wait(output_count>=recovery_deadline);
        if(active_starts-slow_starts<2 || status[0] || !status[2])
            $fatal(1,"Sustained undersupply failed ordered recovery");
        @(negedge src_clk);source_period=768;
        // Reset in the middle of a FIR transaction; old packets/results flush.
        wait(dut.fir_busy); @(negedge src_clk); restart();
        allow_starvation=0;
        wait(output_count>=1700);
        // Audio clock stopped while reset asserts/releases; both pointers
        // reset safely before clock restart. Source queues new frames intact.
        @(negedge audio_clk); audio_clock_run=0;
        restart(); repeat(2000) @(negedge src_clk);
        audio_clock_run=1;
        wait(output_count>=2400);
        // The opposite stopped-clock case: pointer reset asserts even with
        // no source edges. Reader sees no stale packets before source resumes.
        @(negedge src_clk);src_clock_run=0;begin_reset();
        repeat(8) @(negedge audio_clk);reset_async=0;src_running=1;
        repeat(100) @(negedge audio_clk);src_clock_run=1;
        wait(output_count>=2800);
        // Saturation is an explicit sticky fault, never silent packet loss.
        @(negedge audio_clk); audio_clock_run=0;
        restart(); allow_overflow=1;
        repeat(100) @(negedge src_clk);
        if(!src_full || !src_overflow || qwr!=64) $fatal(1,"FIFO overflow fault/capacity");
        audio_clock_run=1; repeat(10) @(negedge audio_clk);
        if(!fault || !status[0] || !status[1]) $fatal(1,"Overflow fault failed CDC/severity");
        @(negedge src_clk); restart(); allow_overflow=0;
        wait(output_count>=3100);
        // A converter that fails its deadline is not recoverable starvation.
        // Retain that fatal diagnostic/mute until the common reset.
        wait(dut.state==3);@(negedge audio_clk);allow_deadline=1;
        force dut.fir_valid=0;
        repeat(600) @(negedge audio_clk);
        if(!fault || !status[0] || !status[3] || output_frame!==0)
            $fatal(1,"Missed deadline lost fatal status/mute");
        release dut.fir_valid;
        @(negedge src_clk);restart();allow_deadline=0;
        wait(output_count>=3350);
        if(fault) $fatal(1,"Common reset did not clear audio diagnostics");
        if(total_packets<2000 || total_results<2300 || pauses!=1 || recoveries!=2) $fatal(1,"Insufficient bridge coverage");
        $display("PASS audio bridge: %0d FIFO packets, %0d FIR results, %0d sink ticks, pause/reset/stopped clock/overflow, %0d starvation recoveries",total_packets,total_results,output_count,recoveries);
        $finish;
    end
    initial begin #100000000000000; $fatal(1,"Bridge timeout"); end
endmodule
