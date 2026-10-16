`timescale 1ns/1ps
// The wrapper under test is generated from the exact copied-top patch. Exercise
// phase changes, stopped clocks and loader freeze independently of guest reset.
module tb_reset_release;
    parameter integer HOST_HALF=7, CPU_HALF=11, PHASE=3;
    reg clk_74a=0,clk1x=0,host_enable=1,cpu_enable=1;
    always #(HOST_HALF) if(host_enable) clk_74a=~clk_74a;
    initial begin #(PHASE);forever #(CPU_HALF) if(cpu_enable) clk1x=~clk1x;end
    reg power_reset_n=0,cpu_locked=0,host_run=0,bios_ready=0,freeze_toggle=0;
    wire raw_reset=!power_reset_n || !cpu_locked;
    wire cd_host_reset,cd_core_reset,cpu_reset,freeze_ack,src_reset,dst_reset;
    wire ready,response,request;
    reg disc_pending=0,disc_change_done=0;
    wire swap_change_req,swap_change_done,swap_lid_open,swap_busy,swap_select_strobe;
    wire [15:0] swap_slot_id;
    // Real sequencers, with short timers only for this reset-domain test.
    psx_disc_swap #(.OPEN_CYCLES(3),.CLOSE_CYCLES(2)) disc_swap(
        .clk(clk1x),.reset(cd_core_reset),.manual_lid_open(1'b0),.swap_request(1'b0),
        .disc_pending(disc_pending),.quiet(1'b0),.change_req(swap_change_req),.change_done(swap_change_done),
        .lid_open(swap_lid_open),.busy(swap_busy));
    psx_disc_select #(.DISC_SLOT(16'h20)) disc_select(
        .clk(clk1x),.reset(cd_core_reset),.change_req(swap_change_req),.change_done(swap_change_done),
        .disc_slot_id(swap_slot_id),.disc_select_strobe(swap_select_strobe),.disc_change_done(disc_change_done));
    reset_release_top top(.*);
    psx_boot_gate gate(.host_clk(clk_74a),.cpu_clk(clk1x),.reset_async(!power_reset_n),
        .cpu_locked(cpu_locked),.host_run(host_run),.bios_ready(bios_ready),
        .freeze_toggle(freeze_toggle),.cpu_reset(cpu_reset),.freeze_ack(freeze_ack));
    psx_mem_cdc cdc(.src_clk(clk_74a),.dst_clk(clk1x),.reset_async(raw_reset),
        .src_reset(src_reset),.dst_reset(dst_reset),.s_valid(1'b0),.s_write(1'b0),
        .s_ready(ready),.s_addr(25'b0),.s_halfwords(4'b0),.s_wdata(128'b0),.s_byte_en(16'b0),
        .s_rsp_valid(response),.s_rsp_ready(1'b1),.s_rsp_rdata(),.s_rsp_error(),
        .d_valid(request),.d_write(),.d_ready(1'b1),.d_addr(),.d_halfwords(),.d_wdata(),.d_byte_en(),
        .d_rsp_valid(1'b0),.d_rsp_ready(),.d_rsp_rdata(128'b0),.d_rsp_error(1'b0));
    integer host_edges=0,core_edges=0,checks=0,acks=0,swap_selects=0;
    // Keep assertion accounting independent of delayed post-NBA checks, so a
    // raw reset coincident with a clock edge cannot be missed by the oracle.
    always @(posedge raw_reset) begin host_edges=0;core_edges=0;end
    always @(posedge clk_74a) begin
        if(raw_reset) host_edges=0;else host_edges=host_edges+1;
        #0.01;
        if(cd_host_reset !== (host_edges<3) || src_reset !== (host_edges<3))
            $fatal(1,"Host reset release edge mismatch %0d",host_edges);
        if(src_reset && (ready || response)) $fatal(1,"Mailbox active during host reset");
        checks=checks+1;
    end
    always @(posedge clk1x) begin
        if(raw_reset) core_edges=0;else core_edges=core_edges+1;
        #0.01;
        if(cd_core_reset !== (core_edges<3) || dst_reset !== (core_edges<3))
            $fatal(1,"Core reset release edge mismatch %0d",core_edges);
        if(dst_reset && request) $fatal(1,"Mailbox active during core reset");
        if(core_edges<3 && (swap_busy || swap_change_req || swap_select_strobe))
            $fatal(1,"Disc swap active during domain reset");
        if(swap_select_strobe) begin
            if(swap_slot_id!==16'h20) $fatal(1,"Disc selection slot changed");
            swap_selects=swap_selects+1;
        end
        checks=checks+1;
    end
    task automatic token;
        reg expected;
        integer n;
        begin
            @(negedge clk_74a);freeze_toggle=~freeze_toggle;expected=freeze_toggle;
            n=0;
            while(freeze_ack!==expected && n<60) begin @(negedge clk_74a);n=n+1;end
            if(freeze_ack!==expected || !cpu_reset) $fatal(1,"Freeze acknowledgement missing while guest held reset");
            acks=acks+1;
        end
    endtask
    initial begin
        #1;power_reset_n=1;#1;power_reset_n=0;
        repeat(5) @(negedge clk_74a);
        // A stopped CPU clock must keep its reset asserted after raw deassert.
        @(negedge clk1x);cpu_enable=0;
        disc_pending=1;
        #2;cpu_locked=1;power_reset_n=1;
        repeat(12) @(negedge clk_74a);
        if(!cd_core_reset || !dst_reset || cd_host_reset || src_reset || freeze_ack)
            $fatal(1,"Stopped core clock released reset");
        cpu_enable=1;
        repeat(8) @(negedge clk1x);
        token();
        // Load/reload freeze tokens must complete before guest Reset Exit.
        host_run=1;bios_ready=1;
        wait(!cpu_reset);repeat(5) @(negedge clk1x);
        #2;bios_ready=0;#0.01;
        if(!cpu_reset) $fatal(1,"Guest reset did not assert asynchronously");
        if(cd_host_reset || cd_core_reset || src_reset || dst_reset)
            $fatal(1,"Guest reset disturbed transport");
        repeat(6) @(negedge clk1x);token();
        if(!swap_busy || !swap_change_req || swap_selects!=1)
            $fatal(1,"Guest reset disturbed disc swap");
        bios_ready=1;wait(!cpu_reset);
        @(negedge clk1x);disc_pending=0;disc_change_done=1;
        @(negedge clk1x);disc_change_done=0;
        wait(!swap_busy);#0.01;
        if(swap_lid_open || swap_change_req || swap_selects!=1)
            $fatal(1,"Disc swap did not complete once lid=%b request=%b selections=%0d",swap_lid_open,swap_change_req,swap_selects);
        // Assert common reset while host is stopped, then release at varying phases.
        @(negedge clk_74a);host_enable=0;
        #1;power_reset_n=0;host_run=0;bios_ready=0;freeze_toggle=0;
        #0.02;
        if(!cd_host_reset || !cd_core_reset || !src_reset || !dst_reset || !cpu_reset)
            $fatal(1,"Common reset did not assert with stopped clock");
        #3;power_reset_n=1;
        repeat(10) @(negedge clk1x);
        if(!cd_host_reset || !src_reset || cd_core_reset || dst_reset)
            $fatal(1,"Stopped host clock released reset");
        host_enable=1;repeat(8) @(negedge clk_74a);token();
        // PLL unlock is a transport reset; repeat off-edge release and lock loss.
        for(integer i=0;i<9;i=i+1) begin
            #(i+1);cpu_locked=0;
            #0.02;if(!cd_host_reset || !cd_core_reset || !src_reset || !dst_reset)
                $fatal(1,"Unlock reset assertion failed");
            #(i+2);cpu_locked=1;
            repeat(5) @(negedge clk1x);repeat(5) @(negedge clk_74a);
            if(cd_host_reset || cd_core_reset || src_reset || dst_reset)
                $fatal(1,"Unlock reset release failed");
        end
        if(acks!=3 || checks<100) $fatal(1,"Insufficient reset coverage");
        $display("PASS reset release checks=%0d freeze_acks=%0d swap_selects=%0d host=%0d core=%0d phase=%0d",checks,acks,swap_selects,HOST_HALF,CPU_HALF,PHASE);
        $finish;
    end
    initial begin #100000;$fatal(1,"Reset release timeout");end
endmodule
