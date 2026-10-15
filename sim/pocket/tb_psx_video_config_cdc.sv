`timescale 1ns/1ps
module tb;
    parameter real SH=14.76255,DH=9.31219,PHASE=0;
    reg src_clk=0,dst_clk=0,src_run=1,dst_run=1,reset=1,valid=0;
    always #(SH) if(src_run) src_clk=~src_clk;
    initial begin #(PHASE);forever #(DH) if(dst_run) dst_clk=~dst_clk;end
    reg [83:0] data=0;
    wire ready,update,initialized,fault;wire [83:0] actual;
    psx_video_config_cdc dut (.src_clk(src_clk),.dst_clk(dst_clk),.reset_async(reset),
        .src_valid(valid),.src_data(data),.src_ready(ready),.dst_data(actual),
        .dst_update(update),.dst_initialized(initialized),.src_fault(fault));
    reg [83:0] expected[0:131071];real accepted_at[0:131071];
    integer sent=0,received=0,epochs=0,total_sent=0,total_received=0,checks=0,abandoned=0;
    real min_latency=1e20,max_latency=0,latency,normal_min=1e20,normal_max=0;
    reg normal_phase=0;
    integer word_index=0;reg filling=0,expect_overflow=0;
    function automatic [83:0] pattern(input integer n,input integer epoch);
        pattern={4'(n^epoch),16'(n),16'(epoch),16'(n^16'h5a39),16'(~n),16'((n*13)^epoch)};
    endfunction
    always @(negedge src_clk) begin
        if(filling) begin data=pattern(word_index,epochs);word_index++;end
    end
    always @(posedge src_clk) begin
        if(!reset && normal_phase && valid && !ready) $fatal(1,"Nominal stream configuration was not accepted");
        if(!reset && valid && ready) begin
            expected[sent]=data;accepted_at[sent]=$realtime;sent++;total_sent++;
        end
    end
    always @(negedge dst_clk) begin
        if(!reset && !dut.dst_reset && update) begin
            if(received>=sent) $fatal(1,"Unaccepted configuration returned");
            if(actual!==expected[received]) $fatal(1,"Configuration order/data mismatch index=%0d got=%h expected=%h",received,actual,expected[received]);
            latency=$realtime-accepted_at[received];
            if(latency<min_latency)min_latency=latency;if(latency>max_latency)max_latency=latency;
            if(normal_phase) begin if(latency<normal_min)normal_min=latency;if(latency>normal_max)normal_max=latency;end
            received++;total_received++;checks++;
        end
        if(!reset && initialized && !dut.dst_reset && !update && received==0)
            $fatal(1,"Initialized before a coherent configuration");
    end
    always @(negedge src_clk) if(!reset && fault && !expect_overflow) $fatal(1,"Supported video clocks overflowed configuration FIFO");
    task drain;
        integer age;
        begin
            @(negedge src_clk);filling=0;valid=0;
            age=0;
            while(received!=sent) begin @(negedge dst_clk);age++;if(age>100)$fatal(1,"Configuration did not drain");end
        end
    endtask
    task begin_epoch;
        begin
            reset=1;filling=0;valid=0;abandoned+=sent-received;sent=0;received=0;epochs++;
            #200;reset=0;
            repeat(12) @(negedge src_clk);
            if(fault || initialized) $fatal(1,"Reset retained stale state");
            valid=1;filling=1;
        end
    endtask
    initial begin
        begin_epoch();
        normal_phase=1;repeat(20000) @(negedge src_clk);drain();normal_phase=0;
        // Identical states are still individual snapshots: no coalescing.
        @(negedge src_clk);data=84'hd19307a845c639e123aab;valid=1;filling=0;normal_phase=1;
        repeat(1000) @(negedge src_clk);drain();normal_phase=0;
        // Abort an actual outstanding packet, then restart with a new epoch.
        @(negedge src_clk);valid=1;filling=1;
        repeat(100) @(negedge src_clk);
        if(sent==received) $fatal(1,"In-flight reset coverage missing");
        #1;begin_epoch();repeat(100) @(negedge src_clk);drain();
        // Source clock can stop without abandoning accepted snapshots.
        @(negedge src_clk);src_run=0;
        repeat(20) @(negedge dst_clk);src_run=1;
        repeat(5) @(negedge src_clk);valid=1;filling=1;
        repeat(100) @(negedge src_clk);drain();
        // Reset with destination stopped: no pre-reset pointer/data replay.
        @(negedge dst_clk);dst_run=0;
        reset=1;valid=0;filling=0;abandoned+=sent-received;sent=0;received=0;epochs++;
        repeat(8) @(negedge src_clk);reset=0;
        repeat(12) @(negedge src_clk);
        if(ready || fault) $fatal(1,"Stopped destination falsely online after reset");
        dst_run=1;repeat(12) @(negedge src_clk);valid=1;filling=1;
        repeat(1000) @(negedge src_clk);drain();
        // Reset with source stopped: destination must not read its old RAM.
        @(negedge src_clk);src_run=0;
        reset=1;valid=0;filling=0;abandoned+=sent-received;sent=0;received=0;epochs++;
        repeat(8) @(negedge dst_clk);reset=0;
        repeat(20) @(negedge dst_clk);
        if(initialized || update) $fatal(1,"Stopped source replayed stale configuration");
        src_run=1;repeat(12) @(negedge src_clk);valid=1;filling=1;
        repeat(1000) @(negedge src_clk);drain();
        // A finite queue cannot promise losslessness while its consumer is
        // stopped forever. It must latch an explicit fault without overwrite.
        expect_overflow=1;
        @(negedge dst_clk);dst_run=0;
        @(negedge src_clk);valid=1;filling=1;
        repeat(40) @(negedge src_clk);
        if(!fault || ready) $fatal(1,"FIFO exhaustion was not reported");
        filling=0;valid=0;dst_run=1;
        // No accepted packet may be corrupt, including the full-queue case.
        drain();
        if(total_sent!=total_received+abandoned || normal_max>120 || normal_min<1) $fatal(1,"Snapshot conservation/latency failed");
        $display("PASS VIDEO_CONFIG epochs=%0d sent=%0d received=%0d abandoned=%0d checks=%0d normal_latency_ns=%.3f..%.3f stopped_latency_ns_max=%.3f",epochs,total_sent,total_received,abandoned,checks,normal_min,normal_max,max_latency);
        $finish;
    end
    initial begin #3000000;$fatal(1,"Video config timeout");end
endmodule
