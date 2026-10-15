// SPDX-License-Identifier: MIT
// Authored cold/warm reset checks; this is not an APF or game boot test.
wire maintenance_busy=core.component_core.isavestates.savestate_busy;
reg disable_maintenance=0;
initial disable_maintenance=$test$plusargs("disable_maintenance");
reg scratch_repro=0;
initial scratch_repro=$test$plusargs("scratch_reset_repro");
reg unpaused_cold_only=0;
initial unpaused_cold_only=$test$plusargs("unpaused_cold_only");
`ifdef CACHED_SPU_SYSTEM
localparam bit CACHED_SPU=1;
wire spu_cached_idle=!core.component_core.memspu_request &&
    core.component_core.ispu.ispu_ram.fifoout_empty && !core.component_core.ispu.ispu_ram.sample64filled &&
    core.component_core.ispu.ispu_ram.memstate==0 && core.component_core.ispu.ispu_ram.state==0;
`else
localparam bit CACHED_SPU=0;
wire spu_cached_idle=1;
`endif
reg cached_map_fault=0;
initial cached_map_fault=$test$plusargs("cached_map_fault");
assign memory_service_ce=core.component_core.ce || (maintenance_busy && !disable_maintenance);
wire all_memory_idle=shared_idle && main_source_idle && main_service_idle && spu_source_idle && spu_cached_idle &&
    !(|packet_valid) && !(|packet_return) && vram_memory.pending_commands==0 &&
    vram_memory.pending_writes==0 && vram_memory.src_state==0 &&
    vram_memory.cmd_empty && !vram_memory.prefetch_valid && !vram_memory.prefetch_pending &&
    !vram_memory.running && vram_memory.fifo_empty && !ddr3_DOUT_READY;

integer memory_packets[0:2],raw_words[0:3],header_reads=0,paused_spu_requests=0;
integer cache_words=0,dma_words=0,spu_request_age=0;
integer reset_main_age=0,cached_spu_words=0;
reg reset_main_pending=0;
reg reset_spu_pending=0,epoch_done=0;
reg reset_check_pending=0;
integer reset_start_cycle=0,pause_start_cycle=0;
reg warm_started=0;
reg delay_main_reset=0,delayed_main_released=0,header_buffer_seen=0,header_forwarded=0;
integer header_capture_cycle=0;
reg [63:0] header_expected=0;
initial delay_main_reset=$test$plusargs("delay_main_reset");
wire permit_main=!delay_main_reset || !warm_started || delayed_main_released;
// Outside the valid strobe deliberately destroy the raw header data. A
// delayed reply must use retained bits, not the backend's last data value.
wire [63:0] header_input=(delay_main_reset && warm_started && header_buffer_seen && !ddr3_DOUT_READY) ?
    64'hdeadbeef_76543210:ddr3_DOUT;
always @(posedge clk2x) begin
    #1;
    if(delay_main_reset && warm_started) begin
        if(!header_buffer_seen && ddr3_DOUT_READY) begin
            if(!main_reset_hold || main_service_idle)
                $fatal(1,"Delayed old main command escaped the reset fence");
            header_buffer_seen=1;header_expected=ddr3_DOUT;header_capture_cycle=cycles;
        end
        if(header_buffer_seen && !delayed_main_released) begin
            if(!main_reset_hold || core.component_core.main_reset_ready)
                $fatal(1,"Reset header advanced before old main work drained");
            if(cycles-header_capture_cycle>=64) delayed_main_released=1;
        end
        if(header_buffer_seen && !header_forwarded && core.component_core.main_reset_ready) begin
            if(main_reset_hold || !delayed_main_released || !main_source_idle || !main_service_idle ||
               core.component_core.main_reset_read!==header_expected || header_input===header_expected)
                $fatal(1,"Buffered reset header lost ownership or retained data");
            header_forwarded=1;
            $display("RESET_HEADER_BUFFER_PASS capture=%0d forward=%0d data=%h",header_capture_cycle,cycles,header_expected);
        end
    end
end
always @(posedge mem_clk) if(shared_initialized) begin
    if(shared_fault || main_fault || spu_fault || vram_fault || (|packet_error))
        $fatal(1,"Shared connected memory protocol fault shared=%0d main=%0d(source=%0d service=%0d) spu=%0d vram=%0d(input=%0d backend=%0d) packet=%b ce=%0d maintenance=%0d reset=%0d ram_req=%0d ddr_rd=%0d ddr_we=%0d count=%0d",
            shared_fault,main_fault,main_commands.source_fault,main_commands.service_fault,
            spu_fault,vram_fault,vram_memory.input_fault,vram_memory.backend_fault,packet_error,
            core.component_core.ce,maintenance_busy,reset,ram_ena,ddr3_RD,ddr3_WE,ddr3_BURSTCNT);
    for(integer p=0;p<3;p++) if(packet_valid[p] && packet_ready[p]) memory_packets[p]++;
    for(integer p=0;p<4;p++) if(word_valid[p] && word_ready[p]) raw_words[p]++;
    if(CACHED_SPU && word_valid[3] && word_ready[3] && word_write[3] && maintenance_busy) begin
        if(word_addr[3]>=24'h400000 && word_addr[3]<24'h420000)
            $fatal(1,"Cached SPU clear reached legacy scalar window");
        if(word_addr[3]>=24'h2c0000 && word_addr[3]<24'h2e0000) begin
            if(word_mask[3]!=4'hf) $fatal(1,"Cached SPU clear has incomplete byte mask");
            cached_spu_words++;
        end
    end
    if(word_valid[3] && word_ready[3] && !word_write[3] &&
       (word_addr[3]==24'ha00000 || word_addr[3]==24'ha00001 ||
        word_addr[3]==24'hb00000 || word_addr[3]==24'hb00001 ||
        word_addr[3]==24'hc00000 || word_addr[3]==24'hc00001 ||
        word_addr[3]==24'hd00000 || word_addr[3]==24'hd00001)) header_reads++;
    if(shared_idle && (memory.phy.oe_reg || memory.phy.wr_en_d || memory.phy.r_wrdata_en))
        $fatal(1,"Shared physical idle before posted write drained");
end
always @(posedge clk3x) if(cache_wr!=0) cache_words++;
always @(posedge clk1x) begin
    if((warm_started || unpaused_cold_only) && cycles-reset_start_cycle<200)
        $display("RESET_REQUEST warm=%0d age=%0d ce=%0d mce=%0d host_reset=%0d core_reset=%0d ss_busy=%0d ss_state=%0d paused=%0d ram_req=%0d adr=%h cache=%0d toggle=%0d ack=%b retstate=%0d response=%0d rspack=%0d",
            warm_started,cycles-reset_start_cycle,core.component_core.ce,memory_service_ce,reset,
            core.component_core.reset_intern,maintenance_busy,core.component_core.isavestates.state,
            isPaused,ram_ena,ram_Adr,ram_cache,main_commands.request_toggle,
            main_commands.request_ack_sync,main_commands.return_state,
            main_commands.response_sync[1],main_commands.response_ack);
    if(reset_check_pending && cycles-reset_start_cycle>14000000)
        $fatal(1,"Shared reset deadline state=%0d paused=%0d ss_idle=%0d cpu_idle=%0d main_idle=%0d service_idle=%0d",
            core.component_core.isavestates.state,isPaused,core.component_core.ss_idle,
            core.component_core.ss_idle_cpu,main_source_idle,main_service_idle);
    if(epoch_done && !reset_check_pending && cycles-pause_start_cycle>50000)
        $fatal(1,"Shared guest pause/drain deadline paused=%0d ss_idle=%0d cpu_idle=%0d main_idle=%0d service_idle=%0d",
            isPaused,core.component_core.ss_idle,core.component_core.ss_idle_cpu,main_source_idle,main_service_idle);
    if(dma_wr) dma_words++;
    if(ram_ena && maintenance_busy && !memory_service_ce) begin reset_main_pending=1;reset_main_age=0;end
    if(ram_done) reset_main_pending=0;
    if(reset_main_pending) begin
        reset_main_age++;
        if(reset_main_age>5000) $fatal(1,"Shared reset main request stalled while guest paused");
    end
    if(spuram_ena && maintenance_busy) begin
        reset_spu_pending=1;spu_request_age=0;
        if(!core.component_core.ce) paused_spu_requests++;
    end
    if(spuram_done) reset_spu_pending=0;
    if(reset_spu_pending) begin
        spu_request_age++;
        if(spu_request_age>5000) $fatal(1,"Shared reset SPU request stalled while guest paused");
    end
end

task poison_reset_regions;
    // Main/VRAM are adjacent only in compressed MODEL storage, not on the pins.
    for(integer i=0;i<1572864;i++) chip.memory[i]=16'ha5c3;
    for(integer i=1703936;i<1966080;i++) chip.memory[i]=16'h3ca5;
`ifdef CACHED_SPU_SYSTEM
    for(integer i=2228240;i<2490384;i++) chip.memory[i]=16'h91e7;
`endif
    for(integer i=0;i<2048;i++) core.component_core.ispu.ispu_ram.iram_capture.ram[i]=16'hb53c;
    for(integer i=0;i<32768;i++) core.component_core.ispu.ispu_ram.iram_reverb.ram[i]=16'hc5b3;
endtask
task check_reset_commit;
    for(integer i=0;i<1572864;i++)
        if(chip.memory[i]!==0) $fatal(1,"Shared reset main/VRAM not physically cleared index=%0d data=%h",i,chip.memory[i]);
    // spu_ram always routes capture [0,4KiB) and reverb [448KiB,512KiB)
    // to local BRAM. Reserved external aliases are deliberately untouched.
    for(integer i=1705984;i<1933312;i++)
        if(chip.memory[i]!==0) $fatal(1,"Shared reset SPU not physically cleared index=%0d data=%h",i,chip.memory[i]);
    for(integer i=1703936;i<1705984;i++)
        if(chip.memory[i]!==16'h3ca5) $fatal(1,"Shared reset wrote reserved capture backing");
    for(integer i=1933312;i<1966080;i++)
        if(chip.memory[i]!==16'h3ca5) $fatal(1,"Shared reset wrote reserved reverb backing");
`ifdef CACHED_SPU_SYSTEM
    for(integer i=2228240;i<2490384;i++)
        if(chip.memory[i]!==16'h91e7) $fatal(1,"Shared reset corrupted old scalar SPU guard");
`endif
    for(integer i=0;i<2048;i++)
        if(core.component_core.ispu.ispu_ram.iram_capture.ram[i]!==0)
            $fatal(1,"Shared reset SPU capture BRAM not cleared index=%0d",i);
    for(integer i=0;i<32768;i++)
        if(core.component_core.ispu.ispu_ram.iram_reverb.ram[i]!==0)
            $fatal(1,"Shared reset SPU reverb BRAM not cleared index=%0d",i);
    for(integer i=1572864;i<1703936;i++)
        if(chip.memory[i]!==16'h6db7) $fatal(1,"Shared reset corrupted card backing index=%0d",i);
    for(integer i=0;i<131072;i++)
        if({chip.memory[1966080+2*i+1],chip.memory[1966080+2*i]}!==bios_words[i])
            $fatal(1,"Shared reset corrupted BIOS index=%0d",i);
endtask
task check_diagnostic_commit;
    for(integer i=0;i<2648;i++)
        if({chip.memory[2048+2*i+1],chip.memory[2048+2*i]}!==expected[i])
            $fatal(1,"Shared diagnostic committed result mismatch index=%0d",i);
    if({chip.memory[32641],chip.memory[32640]}!==32'h600df00d)
        $fatal(1,"Shared diagnostic sentinel did not physically commit");
endtask
reg [31:0] bios_words[0:131071];
initial begin
    for(integer p=0;p<3;p++) memory_packets[p]=0;
    for(integer p=0;p<4;p++) raw_words[p]=0;
    for(integer i=0;i<131072;i++) bios_words[i]=0;
    $readmemh("boot.hex",bios_words);
    wait(shared_initialized);repeat(8) @(negedge clk1x);
    poison_reset_regions();
    if(delay_main_reset) begin
        // Not a valid savestate size: reset still takes its ordinary cold
        // path. Nonzero bits ensure the reply-buffer comparison is useful.
        chip.memory[2228224]=16'hcdef;chip.memory[2228225]=16'h89ab;
        chip.memory[2228226]=16'h4567;chip.memory[2228227]=16'h0123;
    end
    for(integer epoch=0;epoch<(unpaused_cold_only ? 1:2);epoch++) begin
        integer previous_headers,previous_spu,previous_cached_words;
        previous_headers=header_reads;previous_spu=paused_spu_requests;previous_cached_words=cached_spu_words;
        epoch_done=0;diagnostic=1;checks=0;pause=unpaused_cold_only ? 0:1;reset=1;
        repeat(12) @(negedge clk1x);
        reset_start_cycle=cycles;reset_check_pending=1;warm_started=epoch==1;reset=0;
        wait(maintenance_busy);wait(!maintenance_busy);
        // With pause=0 the guest may already be modifying RAM at reset end.
        // Keep the default paused fixture's full clear check unchanged; this
        // entry probe checks committed diagnostics after the final real pause.
        if(!unpaused_cold_only) wait(all_memory_idle);
        if(!scratch_repro && !unpaused_cold_only) check_reset_commit();
        if(header_reads-previous_headers!=8) $fatal(1,"Shared reset did not read four normalized headers");
        if(!scratch_repro && paused_spu_requests-previous_spu!=(CACHED_SPU ? 0:227328)) $fatal(1,"Shared reset did not service every external paused SPU halfword");
        if(CACHED_SPU && cached_spu_words-previous_cached_words!=113664) $fatal(1,"Cached reset did not commit every external SPU word");
        reset_check_pending=0;
        $display("SHARED_RESET epoch=%0d cycles=%0d cleared_bytes=%0d header_words=%0d paused_spu_requests=%0d",
            epoch,cycles,(scratch_repro || unpaused_cold_only) ? 0:3670016,header_reads-previous_headers,paused_spu_requests-previous_spu);
        if(CACHED_SPU) $display("CACHED_SPU_RESET epoch=%0d physical_byte_base=00b00000 words=%0d old_window_guard_bytes=524288",
            epoch,cached_spu_words-previous_cached_words);
        @(negedge clk1x);pause=0;
        // A guest request is not its completion. Leave CE available until
        // MiSTer grants pause, then wait for consumed returns and pin drain.
        wait(epoch_done);wait(isPaused);wait(all_memory_idle);
        check_diagnostic_commit();
        $display("SHARED_DIAGNOSTIC epoch=%0d cycles=%0d checks=%0d committed_bytes=10596",epoch,cycles,checks);
        if(epoch==0 && !unpaused_cold_only) begin
            @(negedge clk1x);reset=1;poison_reset_regions();
            repeat(12) @(negedge clk1x);
        end
    end
    if(delay_main_reset && !header_forwarded) $fatal(1,"Delayed reset-header coverage was not reached");
    $display("PASS SHARED SDRAM SYSTEM epochs=%0d checks=%0d cycles=%0d main_packets=%0d spu_packets=%0d vram_words=%0d cache_words=%0d dma_words=%0d refreshes=%0d",
        unpaused_cold_only ? 1:2,unpaused_cold_only ? 2648:5296,cycles,
        memory_packets[0],memory_packets[1],raw_words[3],cache_words,dma_words,chip.refreshes);
    $finish;
end
