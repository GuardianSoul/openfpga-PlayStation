`timescale 1ns/1ps
module tb;
    parameter real SH=9.31219,DH=7.381275,PHASE=0;
    reg src_clk=0,dst_clk=0,src_run=1,dst_run=1,reset_async=1;
    always #(SH) if(src_run) src_clk=~src_clk;
    initial begin #(PHASE);forever #(DH) if(dst_run) dst_clk=~dst_clk;end
    reg [38:0] src_data=0;
    wire src_ready,src_fault,dst_initialized,dst_fault;
    wire [38:0] dst_data;
    psx_video_request_cdc dut(.*);
    reg [38:0] queue[0:255];
    integer sent=0,received=0,sequence_id=0,total=0,abandoned=0,duplicates=0;
    reg [38:0] previous=0;
    reg overflow_test=0;
    always @(posedge reset_async) begin
        abandoned+=sent-received;sent=0;received=0;
    end
    always @(negedge src_clk) begin
        sequence_id++;
        if(sequence_id%7!=0)
            src_data={1'(sequence_id),10'(sequence_id*13),9'(sequence_id*7),10'(sequence_id^765),9'(sequence_id*83)};
    end
    always @(posedge src_clk) if(!reset_async && src_ready) begin
        if(sent-received>=255)$fatal(1,"Request scoreboard overflow");
        queue[sent&255]=src_data;sent++;
        if(src_data==previous)duplicates++;
        previous=src_data;
    end
    always @(negedge dst_clk) begin
        if(!reset_async && !dut.channel.dst_reset && dut.channel.dst_update) begin
            if(received>=sent)$fatal(1,"Unaccepted request returned");
            if(dst_data!==queue[received&255])$fatal(1,"Request order/data mismatch got=%h expected=%h",dst_data,queue[received&255]);
            received++;total++;
        end
        if(!overflow_test && !reset_async && src_fault)$fatal(1,"Unexpected request overflow");
    end
    task restart;
        begin
            reset_async=1;repeat(13)@(negedge src_clk);
            reset_async=0;repeat(64)@(negedge src_clk);
            if(!src_ready||src_fault||!dst_initialized||dst_fault)$fatal(1,"Request restart failed");
        end
    endtask
    initial begin
        repeat(16)@(negedge src_clk);reset_async=0;
        repeat(20000)@(negedge src_clk);
        // Common reset during queued traffic, independent of a guest CE.
        restart();
        // Either clock may stop while the shared reset is asserted/released.
        @(negedge dst_clk);dst_run=0;reset_async=1;
        repeat(24)@(negedge src_clk);reset_async=0;
        repeat(12)@(negedge src_clk);dst_run=1;
        repeat(1000)@(negedge src_clk);
        @(negedge src_clk);src_run=0;reset_async=1;
        repeat(24)@(negedge dst_clk);reset_async=0;
        repeat(12)@(negedge dst_clk);src_run=1;
        repeat(1000)@(negedge src_clk);
        // A stopped faster consumer is outside the runtime contract: fault,
        // no overwrite, no silent recovery, followed by explicit common reset.
        overflow_test=1;
        @(negedge dst_clk);dst_run=0;
        repeat(100)@(negedge src_clk);
        if(!src_fault||src_ready)$fatal(1,"Missing request overflow fault");
        dst_run=1;repeat(100)@(negedge src_clk);
        if(!dst_fault||!src_fault||src_ready)$fatal(1,"Missing synchronized/latching request fault");
        restart();overflow_test=0;
        repeat(1000)@(negedge src_clk);
        if(total<20000||duplicates<1000||abandoned<1)$fatal(1,"Request coverage incomplete");
        $display("PASS REQUEST_CDC delivered=%0d repeated=%0d abandoned_by_reset=%0d",total,duplicates,abandoned);
        $finish;
    end
    initial begin #3000000;$fatal(1,"Request CDC timeout");end
endmodule
