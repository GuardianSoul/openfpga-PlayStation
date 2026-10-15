`timescale 1ns/1ps
module tb;
    parameter real SH=14.76255,DH=6.734,DP=0;
    reg sc=0,dc=0,srun=1,drun=1,reset=1;
    always #(SH) if(srun) sc=~sc;
    initial begin #(DP);forever #(DH) if(drun) dc=~dc;end
    reg [68:0] desired=0;
    wire valid;wire [68:0] actual;
    psx_status_cdc dut (.src_clk(sc),.dst_clk(dc),.reset_async(reset),
        .src_data(desired),.dst_valid(valid),.dst_data(actual));
    reg [68:0] model[0:1],held=0;
    reg [1:0] model_valid=0;
    reg old_request,old_ack,old_busy;
    reg [68:0] sampled;
    integer publishes=0,commits=0,settles=0,epochs=0;
    function automatic [68:0] pattern(input [31:0] value);
        pattern={value,~value,value[4:0]};
    endfunction
    // Independently record the source value and destination bank at the
    // publication edge; no DUT RAM value becomes the expected snapshot.
    always @(posedge sc) begin
        old_request=dut.request;old_busy=dut.request!=dut.acknowledge_sync;
        sampled=desired;
        #0.002;
        if(reset || dut.src_reset) model_valid=0;
        else begin
            if(dut.request!=old_request) begin
                if(old_busy) $fatal(1,"Published while snapshot outstanding");
                if(valid && !dut.dst_reset && dut.request==dut.acknowledge)
                    $fatal(1,"Published into exposed status RAM bank");
                model[dut.request]=sampled;model_valid[dut.request]=1;
                held=sampled;publishes++;
            end
            if(dut.request!=dut.acknowledge_sync && dut.snapshots[dut.request]!==held)
                $fatal(1,"Outstanding status RAM overwritten");
        end
    end
    // Quartus maps the synchronous read as a registered address and an
    // unregistered RAM output. Check the delivered word between captures too;
    // writes to its exposed bank would violate this even without a new ACK.
    always @(negedge dc) begin
        #0.004;
        if(valid && !reset && !dut.dst_reset && actual!==model[dut.acknowledge])
            $fatal(1,"Exposed status word changed without a capture");
    end
    always @(posedge dc) begin
        old_ack=dut.acknowledge;
        #0.003;
        if(reset || dut.dst_reset) begin
            if(valid || actual!==69'b0) $fatal(1,"Status reset exposed stale RAM");
        end else if(dut.acknowledge!=old_ack) begin
            if(!model_valid[dut.acknowledge] || !valid || actual!==model[dut.acknowledge])
                $fatal(1,"Torn or wrong status snapshot");
            commits++;
        end
    end
    task settle(input [68:0] value);
        integer age;
        begin
            @(negedge sc);desired=value;#0.004;age=0;
            while(!valid || actual!==value) begin
                @(negedge sc);age++;
                if(age>200) $fatal(1,"Latest status did not arrive");
            end
            settles++;
        end
    endtask
    reg [31:0] rng=32'h91f453ab;
    reg request_before;
    reg [68:0] stopped_value;
    initial begin
        repeat(4) @(negedge sc);reset=0;epochs++;
        settle(0);
        // Exercise every bit both ways, including equal/zero publications.
        for(integer i=0;i<69;i++) begin settle(69'b1<<i);settle(~(69'b1<<i));end
        settle(0);settle(0);
        for(integer i=0;i<4096;i++) begin
            @(negedge sc);rng={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};
            desired=pattern(rng);
        end
        settle(pattern(32'hfe209ba8));
        // A stopped host cannot ACK an outstanding publication or permit its
        // RAM word to be overwritten. Once resumed the latest level arrives.
        @(negedge dc);drun=0;
        repeat(40) begin @(negedge sc);desired=~desired;end
        desired=pattern(32'ha55711ed);
        repeat(12) @(negedge sc);
        if(dut.request==dut.acknowledge_sync) $fatal(1,"Stopped host acknowledged status");
        drun=1;settle(pattern(32'ha55711ed));
        // Invalidate a token with reset asserted while the host clock stops.
        @(negedge dc);drun=0;#1.7;reset=1;epochs++;
        #0.01;if(valid || actual!==0) $fatal(1,"Asynchronous reset did not mask RAM");
        repeat(5) @(negedge sc);reset=0;
        desired=pattern(32'h15132a40);repeat(20) @(negedge sc);
        if(valid) $fatal(1,"Stopped-clock reset exposed stale status");
        drun=1;settle(pattern(32'h15132a40));
        // A stopped source may finish its held transaction; it cannot need CE
        // or another source edge to make the already-published data coherent.
        @(negedge sc);request_before=dut.request;stopped_value=pattern(32'h31ba7658);desired=stopped_value;
        wait(dut.request!=request_before);@(negedge sc);srun=0;
        repeat(20) @(negedge dc);
        if(!valid || actual!==stopped_value) $fatal(1,"Stopped source snapshot lost");
        srun=1;settle(stopped_value);
        if(publishes<200 || commits<200 || settles!=145 || epochs!=2)
            $fatal(1,"Status snapshot coverage missing");
        $display("PASS STATUS_CDC publishes=%0d commits=%0d settles=%0d epochs=%0d",publishes,commits,settles,epochs);
        $finish;
    end
    initial begin #6000000;$fatal(1,"Status test timeout");end
endmodule
