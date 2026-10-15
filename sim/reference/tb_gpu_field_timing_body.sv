// SPDX-License-Identifier: GPL-2.0-only
// Appended after exact generated GPU instances/clocks/memory connections.
integer ticks=0,old_fields=0,new_fields=0,draws=0;
reg old_irq_previous=0,new_irq_previous=0;
integer trace_file;
initial trace_file=$fopen("field-events.csv","w");
always @(negedge clk1x) begin
    ticks++;
    if(!reset && !ss_reset) begin
        if(old_irq_vblank && !old_irq_previous) begin
            old_fields++;
            $fwrite(trace_file,"%0d,reference,%0d,%0d\n",ticks,old_fields,old_video_interlace);
        end
        if(new_irq_vblank && !new_irq_previous) begin
            new_fields++;
            $fwrite(trace_file,"%0d,test,%0d,%0d\n",ticks,new_fields,new_video_interlace);
        end
        old_irq_previous=old_irq_vblank;new_irq_previous=new_irq_vblank;
    end
end
task write_gpu(input [3:0] address,input [31:0] value);
    @(negedge clk1x);while(old_bus_stall || new_bus_stall) @(negedge clk1x);
    bus_addr=address;bus_datawrite=value;bus_write=1;
    @(negedge clk1x);bus_write=0;
endtask
task idle;
    integer waited;
    repeat(20) @(negedge clk1x);waited=0;
    while(!old_ss_idle || !new_ss_idle) begin
        @(negedge clk1x);waited++;
        if(waited>100000) $fatal(1,"Field fixture renderer timeout");
    end
    repeat(20) @(negedge clk1x);
endtask
initial begin : stimulus
    integer old_even,old_odd,new_even,new_odd;
    integer target;
    reg [15:0] a,b;
    repeat(100) @(negedge clk1x);reset=0;ss_reset=0;
    repeat(100) @(negedge clk1x);
    write_gpu(4,32'h00000000);
    write_gpu(4,32'h05000000);write_gpu(4,32'h06c60260);
    write_gpu(4,32'h07040010);write_gpu(4,32'h08000027);
    write_gpu(4,32'h03000000);
    write_gpu(0,32'he1000000);write_gpu(0,32'he3000000);
    write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);write_gpu(0,32'he6000000);idle();
    // Every-field producer versus an intentional every-other-field control.
    // The latter demonstrates a scheduling cause without changing the shader.
    for(integer field=0;field<8;field++) begin
        target=field+2;
        wait(old_fields>=target && new_fields>=target);
        repeat(100) @(negedge clk1x);
        if(field%DRAW_CADENCE==0) begin
            write_gpu(0,32'h600000ff);write_gpu(0,32'h00d20028);
            write_gpu(0,32'h00200020);idle();draws++;
        end
    end
    old_even=0;old_odd=0;new_even=0;new_odd=0;
    for(integer y=210;y<242;y++) for(integer x=40;x<72;x++) begin
        a=16'(old_vram.data[(y*1024+x)>>1]>>((x%2)*16));
        b=16'(new_memory.mem.data[(y*1024+x)>>1]>>((x%2)*16));
        if(a!==b) $fatal(1,"Field fixture pixel mismatch x=%0d y=%0d old=%h new=%h",x,y,a,b);
        if(a!=0 && a!=16'h001f || b!=0 && b!=16'h001f) $fatal(1,"Field fixture color mismatch");
        if(a) begin if(y%2)old_odd++;else old_even++;end
        if(b) begin if(y%2)new_odd++;else new_even++;end
    end
    if(DRAW_CADENCE==1 && (old_even!=512 || old_odd!=512))
        $fatal(1,"Every-field producer lost a parity: even=%0d odd=%0d",old_even,old_odd);
    if(DRAW_CADENCE==2 && old_even+old_odd!=512)
        $fatal(1,"Every-other-field control did not isolate a parity: even=%0d odd=%0d",old_even,old_odd);
    $display("GPU_FIELD PASS candidate=%0d finite=%0d cadence=%0d fields=%0d/%0d draws=%0d pixels=%0d/%0d reference=%0d/%0d ticks=%0d",
        USE_CANDIDATE,FINITE_MEMORY,DRAW_CADENCE,old_fields,new_fields,draws,new_even,new_odd,old_even,old_odd,ticks);
    $finish;
end
initial begin repeat(10000000) @(negedge clk1x);$fatal(1,"Field fixture watchdog");end
endmodule
