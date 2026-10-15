`timescale 1ns/1ps
module tb;
    parameter real SH=6.734,DH=14.76255,DP=0;
    reg sc=0,dc=0,srun=1,drun=1,reset=1;
    always #(SH) if(srun) sc=~sc;
    initial begin #(DP);forever #(DH) if(drun) dc=~dc;end
    reg [3:0] desired=0;
    wire current;wire [3:0] actual;
    psx_config_cdc #(.WIDTH(4)) dut (.src_clk(sc),.dst_clk(dc),.reset_async(reset),
        .src_data(desired),.src_current(current),.dst_data(actual));
    reg [3:0] expected=0,held=0;
    reg was_request=0,was_ack=0,busy_before=0;
    integer epochs=0,publishes=0,commits=0,stable_cases=0,checks=0;
    // Capture each complete source publication; the destination must commit
    // exactly that snapshot, including when desired changes again meanwhile.
    always @(negedge sc) begin
        #0.001;
        if(reset || dut.src_reset) begin was_request=0;busy_before=0;end
        else begin
            checks++;
            if(dut.request!=was_request) begin
                if(busy_before) $fatal(1,"Published while previous snapshot outstanding");
                expected=dut.payload;held=dut.payload;publishes++;
            end
            if(dut.request!=dut.acknowledge_sync && dut.payload!==held)
                $fatal(1,"Outstanding payload was overwritten");
            was_request=dut.request;
            busy_before=dut.request!=dut.acknowledge_sync;
            if(current && (actual!==desired || dut.request!=dut.acknowledge))
                $fatal(1,"Current claimed before destination commit");
        end
    end
    always @(negedge dc) begin
        if(reset || dut.dst_reset) was_ack=0;
        else begin
            checks++;
            if(dut.acknowledge!=was_ack) begin
                if(actual!==expected) $fatal(1,"Torn or wrong configuration snapshot");
                commits++;
            end
            was_ack=dut.acknowledge;
        end
    end
    task settle(input [3:0] value);
        integer age;
        begin
            @(negedge sc);desired=value;#0.002;
            age=0;
            while(!current) begin @(negedge sc);age++;if(age>100) $fatal(1,"Latest configuration did not arrive");end
            if(actual!==value) $fatal(1,"Stable configuration lost");
            stable_cases++;
        end
    endtask
    initial begin
        repeat(4) @(negedge sc);reset=0;epochs++;
        settle(0);
        for(integer a=0;a<16;a++) for(integer b=0;b<16;b++) begin settle(4'(a));settle(4'(b));end
        // Continuous source-clock changes intentionally coalesce; the last
        // complete value must arrive, with no torn intermediate publication.
        for(integer i=0;i<1024;i++) begin @(negedge sc);desired=4'((i*13)^7);end
        settle(10);
        @(negedge dc);drun=0;
        repeat(20) begin @(negedge sc);desired=~desired;end
        @(negedge sc);desired=5;
        repeat(12) @(negedge sc);
        if(current) $fatal(1,"Stopped destination falsely acknowledged new value");
        drun=1;settle(5);
        // Shared reset asserts even when the destination has no clock. It
        // invalidates old tokens and automatically republishes retained data.
        @(negedge dc);drun=0;
        #2;reset=1;epochs++;
        repeat(5) @(negedge sc);reset=0;
        @(negedge sc);desired=15;
        repeat(15) @(negedge sc);
        if(current) $fatal(1,"Stale ACK crossed a stopped-clock reset");
        drun=1;settle(15);
        // Hold source clock after publication: destination may finish, but
        // payload must remain stable until the source sees its fresh ACK.
        @(negedge sc);desired=3;
        wait(dut.request!=dut.acknowledge_sync);
        @(negedge sc);srun=0;
        repeat(15) @(negedge dc);
        if(actual!==3) $fatal(1,"Held source packet was not committed");
        srun=1;settle(3);
        if(publishes<500 || commits<500 || stable_cases!=517) $fatal(1,"Configuration coverage missing");
        $display("PASS CONFIG_CDC publishes=%0d commits=%0d stable=%0d epochs=%0d checks=%0d",publishes,commits,stable_cases,epochs,checks);
        $finish;
    end
    initial begin #3000000;$fatal(1,"Configuration test timeout");end
endmodule
