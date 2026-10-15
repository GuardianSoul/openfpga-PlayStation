// SPDX-License-Identifier: MIT
`timescale 1ns/1ps
module tb_apf_blank_cdc;
    parameter integer PROFILE=0;
    localparam real VH=PROFILE==2 ? 10.0 : PROFILE==3 ? 7.5 : 9.312;
    localparam real CH=PROFILE==2 ? 12.5 : PROFILE==3 ? 17.5 : 14.763;
    reg host_clk=0,cpu_clk=0,video_clk=0;
    always #6.734 host_clk=~host_clk;
    always #(CH) cpu_clk=~cpu_clk;
    initial begin
        #(PROFILE==1 ? 3.117 : 0.023);
        forever #(VH) video_clk=~video_clk;
    end
    reg reset_async=0,host_run=0,bios_ready=0;
    wire cpu_reset,blank_video;
    psx_boot_gate gate(.host_clk(host_clk),.cpu_clk(cpu_clk),.reset_async(reset_async),
        .cpu_locked(1'b1),.host_run(host_run),.bios_ready(bios_ready),
        .freeze_toggle(1'b0),.cpu_reset(cpu_reset),.freeze_ack());
    psx_video_blank_cdc crossing(.video_clk(video_clk),.blank_async(cpu_reset),.blank_video(blank_video));

    reg ce=0,hsync=0,vsync=0,hblank=1,vblank=1,interlace=0;
    reg force_stall=0,weave=1,field_swap=0;
    reg [23:0] color=24'h7f35a6;
    wire hs,vs,de,skip,rhs,rvs,rde,rskip;
    wire [23:0] rgb,rrgb;
    psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.INTERLACE_FEATURE(1),.SLOT480(1)) dut(
        .clk(video_clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),
        .interlace(interlace),.blank(blank_video),.weave(weave),.field_swap(field_swap),
        .rgb_in(color),.hs(hs),.vs(vs),.de(de),.skip(skip),.rgb(rgb));
    // An unblanked APF receives exactly the same pixels/timing/menu settings.
    // Blanking must never alter sync, skip or the scaler's non-pixel words.
    psx_apf_video #(.STEADY_INTERLACE(0),.SLOT_SELECT(1),.INTERLACE_FEATURE(1),.SLOT480(1)) reference(
        .clk(video_clk),.ce(ce),.hsync(hsync),.vsync(vsync),.hblank(hblank),.vblank(vblank),
        .interlace(interlace),.blank(1'b0),.weave(weave),.field_swap(field_swap),
        .rgb_in(color),.hs(rhs),.vs(rvs),.de(rde),.skip(rskip),.rgb(rrgb));

    integer x=0,y=0,frame=0,edge_count=0;
    integer in_events=0,out_events=0,pixel_checks=0,metadata_checks=0,feature_checks=0;
    integer blank_pixels=0,normal_pixels=0,stall_pixels=0;
    real event_time[0:127];
    reg event_data[0:127];
    real last_video_edge=0,last_blank_change=0,delay_ns;
    real min_delay=1.0e9,max_delay=0;
    always @(cpu_reset) if ($realtime>0.001) begin
        if(in_events>=128) $fatal(1,"Too many reset events");
        event_time[in_events]=$realtime;event_data[in_events]=cpu_reset;
        in_events=in_events+1;
    end
    always @(posedge video_clk) begin
        last_video_edge=$realtime;edge_count=edge_count+1;
    end
    always @(blank_video) if ($realtime>0.001) begin
        if(out_events>=in_events || blank_video!==event_data[out_events])
            $fatal(1,"Blank transition missing/reordered reset event");
        if($realtime-last_video_edge>0.002)
            $fatal(1,"Blank changed outside a video edge");
        delay_ns=$realtime-event_time[out_events];
        // A first-stage bypass would expose the consumer before two complete
        // video periods have elapsed. A stalled synchronizer misses the bound.
        if(delay_ns<4*VH-0.002 || delay_ns>6*VH+0.002)
            $fatal(1,"Blank transition outside CDC latency bound: %0.3f",delay_ns);
        if(delay_ns<min_delay) min_delay=delay_ns;
        if(delay_ns>max_delay) max_delay=delay_ns;
        out_events=out_events+1;last_blank_change=$realtime;
    end
    always @(negedge video_clk) begin
        x=x+1;
        if(x==40) begin x=0;y=y+1;end
        if(y==8) begin y=0;frame=frame+1;end
        hsync=x>=36;vsync=y==7 && x<4;
        hblank=x>=32;vblank=y>=6;
        ce=!force_stall && x%3!=0;
        interlace=frame%2;weave=frame%3!=2;field_swap=frame%4>=2;
        color=24'h7f35a6^x;
    end
    always @(posedge video_clk) begin
        #0.001;
        if({hs,vs,de,skip}!=={rhs,rvs,rde,rskip}) $fatal(1,"APF timing/skip changed with blank");
        if(!de) begin
            if(rgb!==rrgb) $fatal(1,"APF scaler metadata changed with blank");
            metadata_checks=metadata_checks+1;
            if(vs && rrgb[1]) feature_checks=feature_checks+1;
        end else if($realtime-last_blank_change>2*VH+0.002) begin
            if(rgb!==(blank_video ? 24'h0 : rrgb)) $fatal(1,"APF pixel blank/unblank failed");
            pixel_checks=pixel_checks+1;
            if(blank_video) blank_pixels=blank_pixels+1;else normal_pixels=normal_pixels+1;
            if(force_stall) stall_pixels=stall_pixels+1;
        end
    end
    integer i;
    initial begin
        // Hold boot reset through a complete active/blank frame first.
        repeat(400) @(negedge video_clk);
        #1.013;bios_ready=1;host_run=1;
        repeat(80) @(negedge video_clk);
        for(i=0;i<24;i=i+1) begin
            force_stall=i%2;
            // Sub-cycle host reset requests still become full CPU resets in
            // the real gate; their phases vary independently of pixel CE.
            #(0.107+(i%9));
            if(i%3==0) begin host_run=0;#(i%4==0 ? 10*VH : 1.011);host_run=1;end
            else if(i%3==1) begin bios_ready=0;#(i%4==0 ? 10*VH : 2.017);bios_ready=1;end
            else begin reset_async=1;#(i%4==0 ? 10*VH : 0.503);reset_async=0;end
            repeat(80) @(negedge video_clk);
        end
        force_stall=0;repeat(600) @(negedge video_clk);
        if(in_events!=49 || out_events!=in_events || pixel_checks<1000 || blank_pixels<100 ||
           normal_pixels<1000 || stall_pixels<100 || metadata_checks<500 || feature_checks<1)
            $fatal(1,"Insufficient APF coverage in=%0d out=%0d pixels=%0d blank=%0d normal=%0d stall=%0d metadata=%0d features=%0d",
                in_events,out_events,pixel_checks,blank_pixels,normal_pixels,stall_pixels,metadata_checks,feature_checks);
        $display("PASS APF_BLANK profile=%0d events=%0d pixels=%0d metadata=%0d features=%0d delay_ns=%0.3f..%0.3f",
            PROFILE,out_events,pixel_checks,metadata_checks,feature_checks,min_delay,max_delay);
        $finish;
    end
    initial begin #1000000;$fatal(1,"APF blank timeout");end
endmodule
