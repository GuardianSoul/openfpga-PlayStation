`timescale 1ns/1ps
module tb;
    parameter real SH=9.31219,DH=14.76255,PHASE=0;
    reg src_clk=0,dst_clk=0,run_src=1,run_dst=1,reset_async=1;
    always #(SH) if(run_src)src_clk=~src_clk;else src_clk=0;
    initial begin #(PHASE);forever begin #(DH);if(run_dst)dst_clk=~dst_clk;else dst_clk=0;end end
    reg [8:0] src_levels=0;
    reg src_dot=0,allow_fault=0;
    wire src_ready,src_fault,dst_dot,dst_update,dst_initialized,dst_fault;
    wire [8:0] dst_levels;
    psx_video_reports_cdc dut(.*);

    // Independent input event publisher; no access to DUT push/pop/pointers.
    reg [9:0] expected[0:131071];
    realtime launched[0:131071];
    integer written=0,read=0,total=0,ticks=0,changes=0,max_pending=0;
    reg published=0;
    reg [8:0] last_source=0,last_dest=0;
    real max_latency=0;
    always @(posedge src_clk or posedge reset_async) begin
        if(reset_async)begin written=0;published=0;last_source=0;end
        else if(src_ready && (src_dot || !published || src_levels!=last_source))begin
            if(written==131072)$fatal(1,"Publisher storage exhausted");
            expected[written]={src_dot,src_levels};launched[written]=$realtime;
            written++;published=1;last_source=src_levels;
        end
    end
    always @(negedge dst_clk or posedge reset_async) begin
        if(reset_async)begin read=0;last_dest=0;end
        else begin
            if(!allow_fault && (src_fault || dst_fault))$fatal(1,"Unexpected report overflow");
            if(dst_update)begin
                if(read>=written || {dst_dot,dst_levels}!==expected[read])
                    $fatal(1,"Report event order/data mismatch n=%0d expected=%h got=%h",read,expected[read],{dst_dot,dst_levels});
                if($realtime-launched[read]>max_latency)max_latency=$realtime-launched[read];
                read++;total++;if(dst_dot)ticks++;else changes++;
            end else if(!dst_fault && dst_initialized && dst_levels!==last_dest)
                $fatal(1,"Report levels changed without a committed packet");
            if(dst_dot && !dst_update)$fatal(1,"Tick without report packet");
            if((!dst_initialized || dst_fault) && (dst_dot || dst_update))
                $fatal(1,"Invalid/faulted report emitted an event");
            last_dest=dst_levels;
            if(!allow_fault && written-read>max_pending)max_pending=written-read;
        end
    end
    task initialize;
        begin
            reset_async=1;src_dot=0;src_levels=0;
            #1;if(dst_dot || dst_initialized || dst_update)$fatal(1,"Common reset did not mask reports");
            run_src=1;run_dst=1;
            repeat(8)@(negedge src_clk);reset_async=0;
            wait(src_ready && dst_initialized);
            repeat(6)@(negedge dst_clk);
        end
    endtask
    task drive(input integer count,input bit burst=0);
        begin
            for(integer i=0;i<count;i++)begin
                @(negedge src_clk);
                src_dot=(i%4)==0;
                if(burst || (i%3)==0)src_levels=src_levels+9'h053;
            end
            @(negedge src_clk);src_dot=0;
        end
    endtask
    task drain;
        begin
            repeat(200)@(negedge dst_clk);
            if(written!=read || src_fault || dst_fault || dst_dot)
                $fatal(1,"Report stream failed to drain w=%0d r=%0d",written,read);
        end
    endtask
    initial begin
        initialize();drive(60000);drain();
        // Shorter than a scanline, dense transition bursts; no rate assumption
        // is made for these finite bursts. Their actual queue use is measured.
        repeat(12)begin drive(16,1);drain();end
        drive(31);initialize();drive(5000);drain();
        // Reset with source clock absent, then release with clocks resuming.
        @(negedge src_clk);run_src=0;reset_async=1;
        repeat(8)@(negedge dst_clk);reset_async=0;
        repeat(8)@(negedge dst_clk);
        if(dst_initialized || dst_dot)$fatal(1,"Stopped-source reset leaked old events");
        run_src=1;wait(src_ready && dst_initialized);drive(7000);drain();
        // A stopped CPU is outside the runtime contract; overflow must become
        // an observable, sticky error, and only common reset may recover it.
        allow_fault=1;@(negedge dst_clk);run_dst=0;drive(100);
        if(!src_fault)$fatal(1,"Missing source report overflow fault");
        run_dst=1;repeat(30)@(negedge dst_clk);
        if(!dst_fault || dst_dot || dst_update || src_ready)
            $fatal(1,"Missing synchronized/sticky report fault");
        initialize();allow_fault=0;drive(6000);drain();
        if(total<30000 || ticks<19000 || changes<10000 || max_latency>1000)
            $fatal(1,"Report coverage/latency missing total=%0d ticks=%0d changes=%0d latency=%0f",total,ticks,changes,max_latency);
        $display("PASS REPORTS_CDC packets=%0d ticks=%0d changes=%0d max_pending=%0d max_latency_ns=%0.3f",total,ticks,changes,max_pending,max_latency);
        $finish;
    end
    initial begin #20000000;$fatal(1,"Report test timeout");end
endmodule
