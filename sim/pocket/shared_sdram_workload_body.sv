// SPDX-License-Identifier: MIT
// Authored guest workload; real CPU/DMA/GPU/SPU/SIO and DDR arbitration.
integer cycles=0,checks=0,max_cycles=30000000,expected_count=0;
reg [31:0] expected[0:2047];
reg audio_started=0,stress=0,epoch_done=0;
wire pair_valid=core.component_core.ispu.sound_out_valid;
wire spu_tick=core.component_core.ispu.spu_tick;
wire sound_timeout=core.component_core.ispu.sound_timeout;
integer warm_ticks=0,stress_ticks=0,stress_pairs=0,stress_timeouts=0,pcm_checks=0;
integer gpu_words=0,spu_reads=0,spu_writes=0,card_reads[0:1],card_writes[0:1];
integer overlap=0,request_age[0:2],max_request_age[0:2],acks[0:2];
wire [2:0] requests={core.component_core.memspu_request,core.component_core.memddr3card2_request,core.component_core.memddr3card1_request};
wire [2:0] acknowledgements={core.component_core.memspu_ack,core.component_core.memddr3card2_ack,core.component_core.memddr3card1_ack};
wire cached_map_fault=0,permit_main=1;
wire [63:0] raw_ddr3_DIN;
reg bad_card_data=0,bad_spu_data=0;
initial begin bad_card_data=$test$plusargs("bad_card_data");bad_spu_data=$test$plusargs("bad_spu_data");end
assign ddr3_DIN=raw_ddr3_DIN ^ ((bad_card_data && ddr3_ADDR[27:20]==1 && ddr3_WE) ? 64'b1:64'b0);
wire [63:0] header_input=ddr3_DOUT ^ ((bad_spu_data && core.component_core.memspu_acknext) ? 64'h00000000_00000001:64'b0);
`ifdef NATIVE_MEMORY_WORKLOAD
assign memory_service_ce=core.component_core.ce || core.component_core.isavestates.savestate_busy;
wire drained=shared_idle && main_source_idle && main_service_idle && spu_source_idle &&
    !requests[2] && core.component_core.ispu.ispu_ram.fifoout_empty &&
    !core.component_core.ispu.ispu_ram.sample64filled && core.component_core.ispu.ispu_ram.memstate==0 &&
    core.component_core.ispu.ispu_ram.state==0 && !(|packet_valid) && !(|packet_return) &&
    vram_memory.pending_commands==0 && vram_memory.pending_writes==0 && vram_memory.src_state==0 &&
    vram_memory.cmd_empty && !vram_memory.prefetch_valid && !vram_memory.prefetch_pending &&
    !vram_memory.running && vram_memory.fifo_empty && !ddr3_DOUT_READY;
`else
wire drained=main_memory.state==0 && video_memory.state==0 && !requests[2];
`endif
task release_program;
`ifdef NATIVE_MEMORY_WORKLOAD
    chip.memory['hff08/2]=1;chip.memory['hff08/2+1]=0;
`else
    main_memory.data['hff08]=1;
`endif
endtask
always @(posedge clk2x) begin
    for(integer p=0;p<3;p++) begin
        if(!requests[p] || acknowledgements[p]) request_age[p]=0;
        else request_age[p]++;
        if(request_age[p]>max_request_age[p]) max_request_age[p]=request_age[p];
        if(stress && acknowledgements[p]) acks[p]++;
    end
    if(stress && core.component_core.vram_pause && !core.component_core.vram_paused) overlap++;
    if(!core.component_core.isavestates.savestate_busy && !ddr3_BUSY) begin
        if(ddr3_ADDR[27:20]==0 && ddr3_WE) gpu_words+=ddr3_BURSTCNT;
        if(ddr3_ADDR[27:20]==3 && ddr3_RD) spu_reads++;
        if(ddr3_ADDR[27:20]==3 && ddr3_WE) spu_writes++;
        for(integer p=0;p<2;p++) if(ddr3_ADDR[27:20]==p+1) begin
            if(ddr3_RD) card_reads[p]++;
            if(ddr3_WE) card_writes[p]++;
        end
    end
end
always @(posedge clk1x) begin
    cycles++;
`ifdef NATIVE_MEMORY_WORKLOAD
    if(shared_fault || main_fault || spu_fault || vram_fault || |packet_error)
        $fatal(1,"Shared workload native memory fault shared=%b main=%b spu=%b vram=%b",shared_fault,main_fault,spu_fault,vram_fault);
`endif
    if(audio_started && !stress && spu_tick) begin
        warm_ticks++;
        if(warm_ticks==512) begin stress=1;release_program();$display("WORKLOAD AUDIO WARM cycles=%0d",cycles);end
    end
    if(stress && !epoch_done) begin
        if(spu_tick) stress_ticks++;
        if(sound_timeout) stress_timeouts++;
        if(pair_valid) begin
            stress_pairs++;
            if($signed(sound_out_left)!=21414 || $signed(sound_out_right)!=-3)
                $fatal(1,"Shared workload PCM mismatch cycle=%0d got=%0d/%0d",cycles,$signed(sound_out_left),$signed(sound_out_right));
            pcm_checks+=2;
        end
    end
    if(ram_ena && !ram_rnw && !core.component_core.isavestates.savestate_busy) begin
        if(ram_Adr>=4096 && ram_Adr<4096+expected_count*4) begin
            if(checks>=expected_count || ram_Adr!=4096+checks*4 || ram_dataWrite!==expected[checks])
                $fatal(1,"Shared workload guest mismatch index=%0d address=%h got=%h want=%h",checks,ram_Adr,ram_dataWrite,expected[checks]);
            checks++;
        end
        if(ram_Adr==25'hff04) begin
            if(ram_dataWrite!=32'h41554449) $fatal(1,"Shared workload marker");
            audio_started=1;
        end
        if(ram_Adr==25'hff00) begin
            if(ram_dataWrite!=32'h600df00d || checks!=expected_count) $fatal(1,"Shared workload incomplete guest checks");
            epoch_done=1;pause=1;
        end
    end
    if(cycles%500000==0) $display("PROGRESS workload cycles=%0d pc=%h checks=%0d audio=%0d/%0d timeout=%0d",cycles,core.component_core.icpu.pc,checks,stress_pairs,stress_ticks,stress_timeouts);
    if(cycles%100000==0 && !audio_started) $display("UPLOAD cycles=%0d pc=%h DMAstate=%0d channel=%0d count=%0d requested=%0d required=%0d fifo=%b ram_req=%b ram_dma=%b SPUcnt=%h FIFOempty=%b busy=%b",
        cycles,core.component_core.icpu.pc,core.component_core.idma.dmastate,core.component_core.idma.activechannel,
        core.component_core.idma.wordcount,core.component_core.idma.requesteddwords,core.component_core.idma.requireddwords,
        core.component_core.idma.fifoin_empty,ram_ena,ram_dma,core.component_core.ispu.cnt,
        core.component_core.ispu.fifoin_empty,core.component_core.ispu.busy);
    if(cycles>=max_cycles) $fatal(1,"Shared workload deadline pc=%h checks=%0d dma=%b spu_status=%h",core.component_core.icpu.pc,checks,core.component_core.dmaon,core.component_core.ispu.stat);
end
initial begin
    for(integer p=0;p<3;p++) begin request_age[p]=0;max_request_age[p]=0;acks[p]=0;end
    for(integer p=0;p<2;p++) begin card_reads[p]=0;card_writes[p]=0;end
    if(!$value$plusargs("expected_count=%d",expected_count)) $fatal(1,"Missing workload expected count");
    if($value$plusargs("max_cycles=%d",max_cycles)) begin end
    $readmemh("expected.hex",expected);
    memcard1_mounted=1;memcard2_mounted=1;memcard1_available=1;memcard2_available=1;
    repeat(100) @(negedge clk1x);
`ifdef NATIVE_MEMORY_WORKLOAD
    wait(shared_initialized);chip.memory['hff08/2]=0;chip.memory['hff08/2+1]=0;
`else
    for(integer p=0;p<2;p++) for(integer w=0;w<32768;w++) video_memory.data[(p+1)*262144+w]=32'h6db76db7;
`endif
    reset=0;pause=0;
    wait(epoch_done);wait(isPaused);wait(drained);repeat(20) @(negedge clk2x);
    if(stress_ticks<512 || pcm_checks<1000 || spu_reads<500 || gpu_words<400000 ||
       card_reads[0]<2 || card_reads[1]<2 || card_writes[0]<1 || card_writes[1]<1 || overlap==0)
        $fatal(1,"Shared workload coverage ticks=%0d PCM=%0d spu=%0d gpu=%0d cards=%0d/%0d/%0d/%0d overlap=%0d",stress_ticks,pcm_checks,spu_reads,gpu_words,card_reads[0],card_reads[1],card_writes[0],card_writes[1],overlap);
    for(integer y=0;y<480;y++) for(integer x=0;x<1008;x++) begin
`ifdef NATIVE_MEMORY_WORKLOAD
        if(chip.memory[1048576+y*1024+x]!==16'h001f) $fatal(1,"Shared workload VRAM pixel %0d/%0d",x,y);
`else
        if((video_memory.data[(y*1024+x)/2]>>(16*(x%2)) & 65535)!==32'h1f) $fatal(1,"Shared workload VRAM pixel %0d/%0d",x,y);
`endif
    end
`ifdef NATIVE_MEMORY_WORKLOAD
    for(integer i=0;i<checks;i++) if({chip.memory[2049+2*i],chip.memory[2048+2*i]}!==expected[i])
        $fatal(1,"Shared workload posted result did not commit index=%0d",i);
`endif
    $display("WORKLOAD_RESULT {\"cycles\":%0d,\"guest_checks\":%0d,\"audio_ticks\":%0d,\"audio_pairs\":%0d,\"audio_timeouts\":%0d,\"pcm_checks\":%0d,\"spu_read_commands\":%0d,\"spu_write_commands\":%0d,\"GPU_write_beats\":%0d,\"GPU_pause_wait_2x_cycles\":%0d,\"card1_read_commands\":%0d,\"card2_read_commands\":%0d,\"card1_write_commands\":%0d,\"card2_write_commands\":%0d,\"max_card1_ack_wait_2x_cycles\":%0d,\"max_card2_ack_wait_2x_cycles\":%0d,\"max_SPU_ack_wait_2x_cycles\":%0d}",cycles,checks,stress_ticks,stress_pairs,stress_timeouts,pcm_checks,spu_reads,spu_writes,gpu_words,overlap,card_reads[0],card_reads[1],card_writes[0],card_writes[1],max_request_age[0],max_request_age[1],max_request_age[2]);
    $display("PASS SHARED MEMORY WORKLOAD");$finish;
end
endmodule
