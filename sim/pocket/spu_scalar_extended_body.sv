// SPDX-License-Identifier: GPL-2.0-only
// Appended only for scalar-buffer coherence/waveform evaluation.
integer scalar_guest_writes=0,scalar_stream_fills=0,stream_pairs=0,pcm_file;
integer scalar_byte_checks=0;
integer keyon_frame=0,first_keyon_frame=-1;
reg [23:0] keyon_seen=0;
bit check_keyon=0;
reg [15:0] scalar_expected=0;
bit scalar_read_pending=0;
bit stream_capture=0,scalar_corrupt_read=0;
string pcm_path;
initial begin
    scalar_corrupt_read=$test$plusargs("scalar_corrupt_read");
    if(!$value$plusargs("pcm_path=%s",pcm_path)) pcm_path="scalar-stream.csv";
    pcm_file=$fopen(pcm_path,"w");if(!pcm_file) $fatal(1,"Scalar SPU PCM file");
end
always @(posedge clk1x) begin
    if(check_keyon && ce) begin
        if(spu_tick) keyon_frame++;
        // Pinned tState enumeration: VOICE_CHECKKEY=10.
        if(spu_core.state==10 && spu_core.keyon[spu_core.index]) begin
            if(first_keyon_frame<0) first_keyon_frame=keyon_frame;
            if(first_keyon_frame!=keyon_frame || keyon_seen[spu_core.index])
                $fatal(1,"Scalar SPU KEYON event alignment");
            keyon_seen[spu_core.index]=1;
        end
    end
    if(reset) scalar_read_pending=0;
    else if(native_memory) begin
        if(sdram_ena) begin
            scalar_read_pending=sdram_rnw;
            scalar_expected={memory.data[int'(sdram_Adr)+1],memory.data[int'(sdram_Adr)]};
            if(!sdram_rnw) for(integer b=0;b<2;b++) if(sdram_be[b])
                memory.data[int'(sdram_Adr)+b]=sdram_dataWrite[8*b+:8];
        end
        if(sdram_done && scalar_read_pending) begin
            if(sdram_dataRead!=={16'b0,scalar_expected})
                $fatal(1,"Scalar SPU byte mismatch got=%h expected=%h",sdram_dataRead,scalar_expected);
            scalar_byte_checks+=2;scalar_read_pending=0;
        end
    end
    if(!reset && sdram_ena && !sdram_rnw) scalar_guest_writes++;
    if(stream_capture && pair_valid && stream_pairs<1024) begin
        $fwrite(pcm_file,"%0d,%0d,%0d\n",stream_pairs,$signed(sound_out_left),$signed(sound_out_right));
        stream_pairs++;
    end
end
always @(posedge mem_clk) if(stream_capture && pv && pr && !pw) scalar_stream_fills++;
task scalar_dma_word(input [15:0] value);
    @(negedge clk1x);dma_writedata=value;dma_write=1;
    @(negedge clk1x);dma_write=0;
endtask
task scalar_extra;
    // Invalidate the live raw buffer via actual guest DMA writes. No KEYOFF,
    // source reset, or bench-driven invalidation precedes the tone change.
    REVERBOFF=1;write_reg(10'h1aa,16'hc020);
    for(integer v=0;v<24;v++) begin
        write_reg(10'h1a6,16'((v+64)*512));
        wait(spu_dmaRequest);scalar_dma_word(16'h0700);
        repeat(7) scalar_dma_word(16'h3333);
        if(native_memory) wait(chip.memory[(v+64)*2048+7]==16'h3333);
        else wait(memory.data[(v+64)*4096+15]==8'h33);
    end
    write_reg(10'h1aa,16'hc000);expected_left=9174;
    measure(3);
    if(scalar_guest_writes<192) $fatal(1,"Scalar SPU live DMA coverage");
    // Install changing payloads only after reset and transport drain. The
    // preceding live rewrite was exclusively through the guest DMA FIFO.
    if(native_memory && contended) begin
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
    // Align stimuli by a voice-scan event. Writing KON at a fixed CPU time
    // during a scan can start different subsets a frame apart on backends
    // with different latency; that is a different guest register timeline.
    wait(SS_idle && spu_core.state==0 && spu_core.sampleticks>4 && spu_core.sampleticks<740);
    check_keyon=1;
    write_reg(10'h188,16'hffff);write_reg(10'h18a,16'h00ff);
    stream_capture=1;
    wait(sdram_ena && sdram_rnw);wait(SS_idle && source_idle);
    @(negedge clk1x);ce=0;repeat(50000) @(negedge clk1x);ce=1;
    measure(4);
    wait(stream_pairs==1024);@(negedge clk1x);stream_capture=0;$fclose(pcm_file);
    if(keyon_seen!==24'hffffff) $fatal(1,"Scalar SPU KEYON coverage");
    check_keyon=0;
    if(native_memory && scalar_stream_fills<200) $fatal(1,"Scalar SPU streaming miss coverage");
    $display("SCALAR_EXTRA guest_writes=%0d stream_fills=%0d stream_pairs=%0d byte_checks=%0d keyon_mask=%h keyon_frame=%0d",scalar_guest_writes,scalar_stream_fills,stream_pairs,scalar_byte_checks,keyon_seen,first_keyon_frame);
endtask
