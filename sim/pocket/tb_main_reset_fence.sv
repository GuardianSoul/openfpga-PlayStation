// SPDX-License-Identifier: MIT
`timescale 1ns/1ps
module tb_main_reset_fence;
    parameter real HALF1=15, HALF3=5, PHASE3=0;
    reg clk1x=0,clk3x=0,run1=1,run3=1;
    always #(HALF1) if(run1) clk1x=!clk1x;
    initial begin #(PHASE3); forever #(HALF3) if(run3) clk3x=!clk3x; end
    reg reset_async=1,reset_request=0,source_idle=1,service_idle=1;
    wire held;
    psx_main_reset_fence dut(.clk1x(clk1x),.clk3x(clk3x),.reset_async(reset_async),
        .reset_request(reset_request),.source_idle(source_idle),.service_idle(service_idle),.hold_reset(held));
    reg watch=0,may_release=0;
    integer cycles=0;
    always @(posedge clk1x) begin
        cycles++;
        #0.001;
        if(watch && !may_release && !held) $fatal(1,"Fence released before fresh drain acknowledgement");
    end
    task cpu_wait(input integer n);repeat(n) @(negedge clk1x);endtask
    task pulse;
        @(negedge clk1x);reset_request=1;
        #0.001;if(!held) $fatal(1,"Reset request did not assert consumer hold immediately");
        @(negedge clk1x);reset_request=0;
    endtask
    task expect_held;
        #0.001;if(!held) $fatal(1,"Outstanding work lost its reset fence");
    endtask
    task release_wait;
        may_release=1;
        fork
            begin wait(!held);end
            begin cpu_wait(20);$fatal(1,"Drained fence did not release");end
        join_any
        disable fork;
        watch=0;
    endtask
    reg token;
    initial begin
        #100;reset_async=0;cpu_wait(12);
        // Last source launch can be accepted on the same edge as reset.
        // The old service-idle value must not certify this unseen launch.
        @(negedge clk1x);reset_request=1;watch=1;may_release=0;
        @(posedge clk1x);#0.001;source_idle=0;
        @(negedge clk1x);reset_request=0;
        cpu_wait(12);expect_held();
        if(dut.request_token!=0) $fatal(1,"Fence token bypassed source ownership acknowledgement");
        // The command is now owned by 3x, but its memory transaction is busy.
        @(negedge clk3x);service_idle=0;
        @(negedge clk1x);source_idle=1;
        cpu_wait(8);expect_held();token=dut.request_token;
        pulse();cpu_wait(4);
        if(dut.request_token!=token) $fatal(1,"Repeated reset reused a token before acknowledgement");
        // A stopped service clock must not turn an old ACK into a new one.
        @(negedge clk3x);run3=0;service_idle=1;
        cpu_wait(12);expect_held();pulse();cpu_wait(4);expect_held();
        // A fresh service ACK can arrive while the source still consumes its
        // held response. Both sides must drain before the CPU may restart.
        @(negedge clk1x);source_idle=0;run3=1;
        cpu_wait(12);expect_held();
        @(negedge clk1x);source_idle=1;release_wait();

        // The next fence uses the opposite token. Stop the source clock while
        // 3x acknowledges, then prove the held CPU cannot release off-clock.
        @(negedge clk3x);service_idle=0;
        watch=1;may_release=0;pulse();cpu_wait(6);expect_held();
        token=dut.request_token;
        @(negedge clk1x);run1=0;
        @(negedge clk3x);service_idle=1;
        repeat(12) @(negedge clk3x);expect_held();
        if(dut.acknowledge_token!=token) $fatal(1,"Service did not acknowledge the fresh opposite token");
        may_release=1;run1=1;release_wait();
        // No reset request: ordinary idle changes must not affect the CPU.
        for(integer i=0;i<32;i++) begin
            @(negedge clk1x);source_idle=!source_idle;
            @(negedge clk3x);service_idle=!service_idle;
            #0.001;if(held) $fatal(1,"Fence changed steady-state execution without a reset request");
        end
        $display("PASS MAIN RESET FENCE cycles=%0d",cycles);$finish;
    end
    initial begin #100000;$fatal(1,"Fence test deadline");end
endmodule
