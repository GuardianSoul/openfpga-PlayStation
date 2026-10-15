// SPDX-License-Identifier: GPL-2.0-only
// Appended to the authored SPU fixture's original ports and clock generator.
bit native_memory=0,contended=0,negative_control=0,withdraw_background=0,unsafe_pause=0;
integer extra_delay=0;
wire [31:0] functional_data,native_data;
wire functional_done,native_done;
assign sdram_dataRead=native_memory ? native_data:functional_data;
assign sdram_done=native_memory ? native_done:functional_done;
reg transport_reset=1;
initial begin #100000000;transport_reset=0;end
reg mem_clk=0,mem_fast_clk=1;
always #8000000 mem_clk=~mem_clk;
always #4000000 mem_fast_clk=~mem_fast_clk;
wire mem_forwarded_clk=~mem_fast_clk;
wire initialized,client_reset,backend_idle,shared_fault,source_idle,adapter_fault;
wire pv,pw,pr,rv,rr,re;
wire [24:0] pa;wire [3:0] pn;wire [127:0] pd,rd;wire [15:0] pb;
wire [3:0] valid,write,ready,rvalid;
wire [3:0][23:0] addr;
wire [3:0][31:0] data;
wire [3:0][3:0] byte_en;
wire [31:0] rdata;
wire [12:0] dram_a;wire [1:0] dram_ba,dram_dqm;
wire dram_ras_n,dram_cas_n,dram_we_n,dram_cke,dram_clk;
wire [15:0] dram_dq;
reg background=0;
integer delay_count=0,bg_accepted=0,bg_returned=0;
wire permit=delay_count>=extra_delay;
always @(posedge mem_clk) begin
    if(!pv) delay_count<=0;
    else if(delay_count<extra_delay) delay_count<=delay_count+1;
    for(integer c=0;c<4;c++) if(c!=1) begin
        if(valid[c] && ready[c]) bg_accepted++;
        if(rvalid[c]) begin
            if(rdata!==32'h3ca53ca5) $fatal(1,"SPU contention background read mismatch");
            bg_returned++;
        end
    end
    if(shared_fault || adapter_fault) $fatal(1,"SPU native memory protocol fault shared=%b adapter=%b",shared_fault,adapter_fault);
end
// CACHED_MEMORY_PORT
`ifndef CACHED_SPU
psx_spu_sdram_adapter adapter (
    .clk1x(clk1x),.mem_clk(mem_clk),.reset_async(transport_reset),.core_ce(ce),.discard_returns(reset),
    .request(sdram_ena && native_memory),.rnw(sdram_rnw),.addr(sdram_Adr),.data(sdram_dataWrite),.byte_en(sdram_be),
    .done(native_done),.read_data(native_data),.source_idle(source_idle),.fault(adapter_fault),
    .p_valid(pv),.p_write(pw),.p_ready(pr),.p_addr(pa),.p_halfwords(pn),.p_wdata(pd),.p_byte_en(pb),
    .p_rsp_valid(rv),.p_rsp_error(re),.p_rsp_rdata(rd),.p_rsp_ready(rr));
wire front_ready;
assign pr=front_ready && initialized && permit;
psx_mem_bridge #(.PIPELINE_READS(1)) front (
    .clk(mem_clk),.rst(client_reset),.req_valid(pv && initialized && permit),.req_ready(front_ready),
    .req_write(pw),.req_addr(pa),.req_halfwords(pn),.req_wdata(pd),.req_byte_en(pb),
    .rsp_valid(rv),.rsp_ready(rr),.rsp_rdata(rd),.rsp_error(re),
    .mem_valid(valid[1]),.mem_ready(ready[1]),.mem_write(write[1]),.mem_addr(addr[1]),
    .mem_wdata(data[1]),.mem_byte_en(byte_en[1]),.mem_rvalid(rvalid[1]),.mem_rdata(rdata));
`endif
genvar c;
generate for(c=0;c<4;c++) if(c!=1) begin: load
    // Quiescence stops new requests, retaining any unaccepted held command.
    reg offered=0;
    always @(posedge mem_clk) begin
        if(client_reset || !initialized) offered<=0;
        else if(!offered || ready[c]) offered<=background;
    end
    assign valid[c]=withdraw_background ? background && initialized:offered;
    assign write[c]=0,addr[c]=24'h100+c*24'h100;
    assign data[c]=0,byte_en[c]=4'hf;
end endgenerate
psx_sdram_shared memory_native (
    .clk_sys(mem_clk),.clk_mem(mem_fast_clk),.clk_dram(mem_forwarded_clk),.reset_async(transport_reset),
    .initialized(initialized),.client_reset(client_reset),.backend_idle(backend_idle),.fault(shared_fault),
    .valid(valid),.write(write),.ready(ready),.addr(addr),.data(data),.byte_en(byte_en),.rvalid(rvalid),.rdata(rdata),
    .dram_a(dram_a),.dram_ba(dram_ba),.dram_dqm(dram_dqm),.dram_ras_n(dram_ras_n),
    .dram_cas_n(dram_cas_n),.dram_we_n(dram_we_n),.dram_cke(dram_cke),.dram_dq(dram_dq),.dram_clk(dram_clk));
sdr_pin_model chip (.clk(dram_clk),.cke(dram_cke),.ras_n(dram_ras_n),.cas_n(dram_cas_n),.we_n(dram_we_n),
    .address(dram_a),.bank(dram_ba),.dm(dram_dqm),.host_oe(memory_native.phy.oe_reg),.dq(dram_dq));

integer cycles=0,phase=-1,active_cycles=0,ticks=0,pairs=0,timeouts=0,pcm_checks=0;
integer reads=0,writes=0,latency_sum=0,latency_max=0,request_at=-1,last_pair=-1,min_gap=100000000,max_gap=0;
integer expected_left=21414;
integer test_pitch=4096;
bit scalar_stream=0;
initial begin scalar_stream=$test$plusargs("scalar_stream");if($value$plusargs("pitch=%d",test_pitch)) begin end end
integer scalar_trace=0;
reg [8191:0] scalar_trace_path;
initial if($value$plusargs("scalar_trace=%s",scalar_trace_path)) scalar_trace=$fopen(scalar_trace_path,"w");
// AUDIO_BRIDGE
bit measuring=0;
always @(posedge clk1x) begin
    cycles++;
    if(measuring) begin
        active_cycles++;
        if(spu_tick) ticks++;
        if(sound_timeout) timeouts++;
        if(pair_valid) begin
            pairs++;
            if(last_pair>=0) begin
                if(cycles-last_pair<min_gap) min_gap=cycles-last_pair;
                if(cycles-last_pair>max_gap) max_gap=cycles-last_pair;
            end
            last_pair=cycles;
            if(phase>0 && phase<4 && !negative_control) begin
                if($signed(sound_out_left)!=expected_left || $signed(sound_out_right)!=-3)
                    $fatal(1,"SPU native constant-voice PCM mismatch %d/%d",$signed(sound_out_left),$signed(sound_out_right));
                pcm_checks+=2;
            end
        end
    end
    if(!reset && sdram_ena) begin
        if(scalar_trace) $fwrite(scalar_trace,"%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",cycles,phase,measuring,sdram_rnw,sdram_Adr,sdram_dataWrite,sdram_be);
        if(request_at>=0) $fatal(1,"SPU overlapped halfword request");
        request_at=cycles;
        if(measuring) begin if(sdram_rnw) reads++;else writes++;end
    end
    if(sdram_done) begin
        if(request_at<0) $fatal(1,"SPU unexpected halfword return");
        if(measuring) begin
            latency_sum+=cycles-request_at;
            if(cycles-request_at>latency_max) latency_max=cycles-request_at;
        end
        request_at=-1;
    end
end
task write_reg(input [9:0] address,input [15:0] value);
    @(negedge clk1x);bus_addr=address;bus_dataWrite=value;bus_write=1;
    @(negedge clk1x);bus_write=0;
endtask
task measure(input integer which);
    // Warm up outside the reported interval; this measures steady state.
    repeat(512) begin @(posedge spu_tick);@(negedge clk1x);end
    phase=which;active_cycles=0;ticks=0;pairs=0;timeouts=0;pcm_checks=0;
    reads=0;writes=0;latency_sum=0;latency_max=0;last_pair=-1;min_gap=100000000;max_gap=0;
    measuring=1;
    repeat(512) begin @(posedge spu_tick);@(negedge clk1x);end
    measuring=0;
    if(unsafe_pause && which==4 && pairs==0) $fatal(1,"Cached SPU unsafe pause lost completion");
    if(active_cycles!=512*768 || ticks!=512 || pairs==0) $fatal(1,"SPU cadence coverage");
    $display("CADENCE {\"phase\":%0d,\"active_cpu_cycles\":%0d,\"ticks\":%0d,\"pairs\":%0d,\"timeouts\":%0d,\"pcm_checks\":%0d,\"reads\":%0d,\"writes\":%0d,\"latency_sum_cpu_cycles\":%0d,\"max_latency_cpu_cycles\":%0d,\"min_pair_gap_cpu_cycles\":%0d,\"max_pair_gap_cpu_cycles\":%0d,\"audio_deadline_met\":%0s}",
      phase,active_cycles,ticks,pairs,timeouts,pcm_checks,reads,writes,latency_sum,latency_max,min_gap,max_gap,
      timeouts==0 && pairs>=ticks-1 ? "true":"false");
endtask
`ifdef CACHED_SPU
task dma_word(input [15:0] value);
    @(negedge clk1x);dma_writedata=value;dma_write=1;
    @(negedge clk1x);dma_write=0;
endtask
task cached_extra;
    // Rewrite every currently cached loop through the guest DMA FIFO, without
    // KEYOFF, reset, or an explicit cache invalidate from the test bench.
    REVERBOFF=1;write_reg(10'h1aa,16'hc020);
    for(integer v=0;v<24;v++) begin
        write_reg(10'h1a6,16'((v+64)*512));
        wait(spu_dmaRequest);dma_word(16'h0700);
        repeat(7) dma_word(16'h3333);
        wait(memory.data[(v+64)*4096+15]==8'h33);
    end
    write_reg(10'h1aa,16'hc000);expected_left=9174;
    measure(3);
    if(cached_writes<48) $fatal(1,"Cached SPU DMA write coverage");
    // Replace the authored banks only while the whole SPU is held reset and
    // transport is drained. Real live writes above use only the guest FIFO.
    if(native_memory && contended) begin
        // Exercise quiescence while a background request is actually held.
        wait(memory_native.locked && memory_native.locked_owner!=1);
        @(negedge mem_clk);reset=1;SS_reset=1;background=0;
    end else begin @(negedge clk1x);reset=1;SS_reset=1;background=0;end
    wait(source_idle && backend_idle);repeat(20) @(negedge mem_clk);
    for(integer v=0;v<24;v++) for(integer b=0;b<64;b++) begin
        memory.data[(v+64)*4096+b*16]=0;
        memory.data[(v+64)*4096+b*16+1]=8'(b==0 ? 4:b==63 ? 3:0);
        chip.memory[(v+64)*2048+b*8]=16'((b==0 ? 4:b==63 ? 3:0)<<8);
        for(integer i=1;i<8;i++) begin
            memory.data[(v+64)*4096+b*16+i*2]=8'h11*8'(b%7+1);
            memory.data[(v+64)*4096+b*16+i*2+1]=8'h11*8'(b%7+1);
            chip.memory[(v+64)*2048+b*8+i]=16'h1111*16'(b%7+1);
        end
    end
    repeat(100) @(negedge clk1x);reset=0;SS_reset=0;
    background=native_memory && contended;
    wait(!spu_core.ss_voice_loading);repeat(8) @(negedge clk1x);
    write_reg(10'h180,16'h1000);write_reg(10'h182,16'h1000);
    for(integer v=0;v<24;v++) begin
        write_reg(10'(v*16),16'h0800);write_reg(10'(v*16+2),v%2 ? 16'h7800:16'h0800);
        write_reg(10'(v*16+4),16'h4000);write_reg(10'(v*16+6),16'((v+64)*512));
        write_reg(10'(v*16+8),16'h00ff);write_reg(10'(v*16+10),16'h1fc0);
        write_reg(10'(v*16+14),16'((v+64)*512));
    end
    write_reg(10'h1aa,16'hc000);
    write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
    stream_capture=1;
    // Match psx_top's actual pause contract: CE may fall only after SPU idle.
    // The negative control deliberately violates it during a cold cache fill.
    wait(mem_request && mem_RD);
    if(!unsafe_pause) wait(SS_idle);
    @(negedge clk1x);ce=0;
    repeat(50000) @(negedge clk1x);ce=1;
    measure(4);
    wait(stream_pairs==1024);@(negedge clk1x);stream_capture=0;$fclose(pcm_file);
    if(stream_fills<200) $fatal(1,"Cached SPU streaming miss coverage");
    check_audio_bridge();
    $display("CACHED fills=%0d writes=%0d stream_fills=%0d stream_pairs=%0d byte_checks=%0d",cached_reads,cached_writes,stream_fills,stream_pairs,cached_byte_checks);
endtask
`endif
initial begin
    native_memory=$test$plusargs("native");contended=$test$plusargs("contended");
    negative_control=$test$plusargs("negative_control");
    withdraw_background=$test$plusargs("withdraw_background");
    unsafe_pause=$test$plusargs("unsafe_pause");
    if($value$plusargs("delay=%d",extra_delay)) begin end
`ifdef CACHED_SPU
    useSDRAM=0;
`endif
    wait(initialized);repeat(10) @(negedge clk1x);
    for(integer v=0;v<24;v++) begin
        memory.data[(v+64)*4096]=0;memory.data[(v+64)*4096+1]=7;
        for(integer i=2;i<16;i++) memory.data[(v+64)*4096+i]=8'h77;
        if(scalar_stream) for(integer b=0;b<64;b++) begin
            memory.data[(v+64)*4096+b*16]=0;
            memory.data[(v+64)*4096+b*16+1]=8'(b==0 ? 4:b==63 ? 3:0);
            chip.memory[(v+64)*2048+b*8]=16'((b==0 ? 4:b==63 ? 3:0)<<8);
            for(integer i=2;i<16;i++) memory.data[(v+64)*4096+b*16+i]=8'h77;
            for(integer i=1;i<8;i++) chip.memory[(v+64)*2048+b*8+i]=16'h7777;
        end
    end
    reset=0;SS_reset=0;wait(!spu_core.ss_voice_loading);repeat(8) @(negedge clk1x);
    background=native_memory && contended;
    write_reg(10'h180,16'h1000);write_reg(10'h182,16'h1000);
    write_reg(10'h1aa,16'hc000);
    measure(0);
`ifdef SCALAR_BUFFER_SPU_AUDIO
    wait(SS_idle && source_idle);@(negedge clk1x);ce=0;
    repeat(50000) @(negedge clk1x);ce=1;
`endif
    for(integer v=0;v<24;v++) begin
        write_reg(10'(v*16),16'h0800);write_reg(10'(v*16+2),v%2 ? 16'h7800:16'h0800);
        write_reg(10'(v*16+4),16'(test_pitch));write_reg(10'(v*16+6),16'((v+64)*512));
        write_reg(10'(v*16+8),16'h00ff);write_reg(10'(v*16+10),16'h1fc0);
        write_reg(10'(v*16+14),16'((v+64)*512));
    end
    write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
    measure(1);
    REVERBOFF=0;
    write_reg(10'h198,16'hffff);write_reg(10'h19a,16'h00ff);
    write_reg(10'h1aa,16'hc080);
    measure(2);
`ifdef CACHED_SPU
    cached_extra();
`endif
    reset=1;background=0;
    wait(source_idle && backend_idle);repeat(20) @(negedge mem_clk);
    if(bg_accepted!=bg_returned || (contended && bg_accepted<1000)) $fatal(1,"SPU background drain coverage");
`ifdef SCALAR_BUFFER_SPU_AUDIO
    if(!negative_control) check_audio_bridge();
`endif
    $display("PASS SPU memory measurement native=%0d contention=%0d bg=%0d pins=%0d/%0d refresh=%0d",
        native_memory,contended,bg_returned,chip.reads,chip.writes,chip.refreshes);
    $finish;
end
initial begin repeat(12000000) @(negedge clk1x);$fatal(1,"SPU memory measurement timeout");end
endmodule
