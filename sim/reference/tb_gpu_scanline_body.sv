// SPDX-License-Identifier: GPL-2.0-only
// Appended to a generated one-GPU fixture. Authored pixels, no BIOS/disc.
integer ticks=0,fields=0,accepted_beats=0,read_beats=0,scanout_read_beats=0;
reg readback_active=0;
reg irq_previous=0;
integer events;
initial events=$fopen("scanline-events.csv","w");
always @(negedge clk1x) begin
    ticks++;
    if(!reset && !ss_reset) begin
        if(irq_vblank && !irq_previous) begin
            fields++;
            $fwrite(events,"%0d,field,%0d,%0d\n",ticks,fields,video_interlace);
        end
        irq_previous=irq_vblank;
    end
end
always @(posedge clk2x) if(!reset) begin
    if((vram_rd || vram_we) && !vram_busy) accepted_beats+=vram_burstcnt;
    if(vram_dout_ready) begin
        read_beats++;
        if(!readback_active) scanout_read_beats++;
    end
    if(native_fault && MEMORY==2) $fatal(1,"Native posted VRAM fault");
    if(errorgpu || errorfifo || errorline || errorrect || errorpoly || errormask)
        $fatal(1,"GPU scanline internal error");
end
task write_gpu(input [3:0] address,input [31:0] value);
    @(negedge clk1x);while(bus_stall) @(negedge clk1x);
    bus_addr=address;bus_datawrite=value;bus_write=1;
    @(negedge clk1x);bus_write=0;
endtask
task read_gpu(input [3:0] address,output [31:0] value);
    @(negedge clk1x);while(bus_stall) @(negedge clk1x);
    bus_addr=address;bus_read=1;
    @(negedge clk1x);value=bus_dataread;bus_read=0;
endtask
task idle;
    integer waited;
    repeat(20) @(negedge clk1x);waited=0;
    while(!ss_idle) begin
        @(negedge clk1x);waited++;
        if(waited>1000000) $fatal(1,"Scanline renderer timeout");
    end
    repeat(20) @(negedge clk1x);
endtask
function automatic [15:0] pixel(input integer x,input integer y);
    pixel={1'b0,5'(x+y*3),5'(x*3+y),5'(x^y)};
endfunction
// One four-word DMA burst per 16 CPU ticks, each word one CPU tick apart.
// Request is sampled at the start of a burst, matching the native DMA port;
// this is a bounded source fixture, not a full CPU/main/SPU timing model.
task upload_line(input integer y);
    integer begun;
    write_gpu(0,32'h01000000);
    write_gpu(0,32'ha0000000);
    write_gpu(0,32'(y<<16));
    write_gpu(0,32'h00010280);
    dma_gpu_waiting=1;
    for(integer group=0;group<80;group++) begin
        @(negedge clk1x);
        while(!gpu_dmarequest) @(negedge clk1x);
        begun=ticks;
        for(integer word=0;word<4;word++) begin
            dma_gpu_write={pixel((group*4+word)*2+1,y),pixel((group*4+word)*2,y)};
            dma_gpu_writeena=1;
            @(negedge clk1x);
        end
        dma_gpu_writeena=0;
        while(ticks-begun<16) @(negedge clk1x);
    end
    dma_gpu_waiting=0;
    idle();
    repeat(60) @(negedge clk1x); // explicit authored per-line host overhead
endtask
// Force the posted writes to retire through a native GPU readback, and check
// every word of this line through the GPU's public CPU port.
task fence_and_readback(input integer y);
    reg [31:0] value;
    readback_active=1;
    write_gpu(0,32'hc0000000);
    write_gpu(0,32'(y<<16));
    write_gpu(0,32'h00010280);
    for(integer word=0;word<320;word++) begin
        do begin read_gpu(4,value); end while(!value[27]);
        read_gpu(0,value);
        if(value!=={pixel(word*2+1,y),pixel(word*2,y)})
            $fatal(1,"GPU scanline readback mismatch y=%0d word=%0d got=%08x",y,word,value);
    end
    idle();
    readback_active=0;
endtask
initial begin : stimulus
    integer start,elapsed,first_fields;
    reg [15:0] expected;
    reg [31:0] status;
    repeat(100) @(negedge clk1x);reset=0;ss_reset=0;
    repeat(20000) @(negedge clk1x); // exceed physical 256us initialization
    write_gpu(4,32'h00000000);
    write_gpu(4,32'h05000000);write_gpu(4,32'h06c60260);
    write_gpu(4,32'h07040010);write_gpu(4,32'h08000027);
    write_gpu(4,32'h03000000);write_gpu(4,32'h04000002);
    write_gpu(0,32'he1000000);write_gpu(0,32'he3000000);
    write_gpu(0,32'he407ffff);write_gpu(0,32'he5000000);write_gpu(0,32'he6000000);
    dmaon=1;idle();
    wait(fields>=2);
    for(integer parity=0;parity<2;parity++) begin
        start=ticks;first_fields=fields;
        for(integer line=0;line<240;line++) begin
            $fwrite(events,"%0d,upload,%0d,%0d\n",ticks,line*2+parity,fields);
            upload_line(line*2+parity);
        end
        elapsed=ticks-start;
        fence_and_readback(478+parity);
        $display("GPU_SCANLINE_SWEEP memory=%0d parity=%0d upload_ticks=%0d fields_elapsed=%0d readback=320 deadline_ticks=564062 within_field=%0d",
                 MEMORY,parity,elapsed,fields-first_fields,elapsed<=564062);
    end
    for(integer y=0;y<512;y++) for(integer x=0;x<1024;x++) begin
        peek_addr=19'(y*1024+x);#1;
        expected=(x<640 && y<480) ? pixel(x,y) : 0;
        if(peek_data!==expected) $fatal(1,"Full VRAM pixel mismatch x=%0d y=%0d got=%04x expected=%04x",x,y,peek_data,expected);
    end
    if(scanout_read_beats<1000) $fatal(1,"Active scanout traffic missing");
    $display("GPU_SCANLINE PASS memory=%0d fields=%0d accepted_beats=%0d read_beats=%0d scanout_read_beats=%0d checked_pixels=524288 ticks=%0d",
             MEMORY,fields,accepted_beats,read_beats,scanout_read_beats,ticks);
    $finish;
end
initial begin repeat(10000000) @(negedge clk1x);$fatal(1,"GPU scanline watchdog");end
endmodule
