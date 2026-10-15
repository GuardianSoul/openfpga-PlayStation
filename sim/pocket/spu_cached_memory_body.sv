// SPDX-License-Identifier: GPL-2.0-only
// Test-only one-client form of psx_top's DDR arbiter contract: accept once,
// pulse ack, wait until the producer releases request (through all read data).
// The real SPU/cache, burst CDC frontend, controller/PHY/pins remain unchanged.
reg held=0,ack_pulse=0;
wire burst_busy,burst_fault,burst_ready;
wire [63:0] burst_data;
wire issue=mem_request && !held && permit && (!native_memory || !burst_busy);
assign pv=mem_request && !held;
assign native_data=0,native_done=0;
assign adapter_fault=burst_fault;
assign source_idle=!held && !mem_request &&
    (!native_memory || (burst_port.pending_commands==0 && burst_port.pending_writes==0 &&
                       burst_port.src_state==0 && !burst_port.running && !burst_port.prefetch_pending && !burst_port.prefetch_valid));
reg [63:0] functional_burst_data=0;
reg functional_burst_ready=0;
integer functional_wait=0,functional_remaining=0,functional_address=0;
integer read_address=0,read_remaining=0;
integer cached_reads=0,cached_writes=0,cached_byte_checks=0,stream_fills=0,stream_pairs=0;
bit corrupt_read=0,drop_writes=0,stream_capture=0;
string pcm_path;
integer pcm_file;
wire [63:0] selected_burst=(native_memory ? burst_data:functional_burst_data) ^ (corrupt_read ? 64'b1:64'b0);
assign mem_ack=ack_pulse;
assign mem_DOUT=selected_burst;
assign mem_DOUT_READY=native_memory ? burst_ready:functional_burst_ready;
initial begin
    corrupt_read=$test$plusargs("corrupt_read");drop_writes=$test$plusargs("drop_writes");
    if(!$value$plusargs("pcm_path=%s",pcm_path)) pcm_path="stream.csv";
    pcm_file=$fopen(pcm_path,"w");if(!pcm_file) $fatal(1,"Cached SPU PCM file");
end
always @(posedge clk1x) if(stream_capture && pair_valid && stream_pairs<1024) begin
    $fwrite(pcm_file,"%0d,%0d,%0d\n",stream_pairs,$signed(sound_out_left),$signed(sound_out_right));
    stream_pairs++;
end
always @(posedge clk2x) begin
    ack_pulse<=0;functional_burst_ready<=0;
    if(transport_reset) begin held<=0;functional_wait=0;functional_remaining=0;read_remaining=0;end
    else begin
        if(!mem_request) held<=0;
        if(issue) begin
            held<=1;ack_pulse<=1;
            if(mem_RD==mem_WE || mem_ADDR[2:0]!=0 || mem_BURSTCNT==0)
                $fatal(1,"Cached SPU malformed DDR request");
            if(mem_RD) begin
                if(read_remaining!=0) $fatal(1,"Cached SPU overlapping read burst");
                cached_reads++;if(stream_capture) stream_fills++;
                read_address=int'(mem_ADDR);read_remaining=int'(mem_BURSTCNT);
                if(!native_memory) begin
                    functional_wait=10;functional_remaining=int'(mem_BURSTCNT);functional_address=int'(mem_ADDR);
                end
            end else begin
                cached_writes++;
                if(mem_BURSTCNT!=1) $fatal(1,"Cached SPU unexpected write count");
                for(integer i=0;i<8;i++) if(mem_BE[i]) memory.data[int'(mem_ADDR)+i]=mem_DIN[i*8+:8];
            end
        end
        if(mem_DOUT_READY) begin
            if(read_remaining==0) $fatal(1,"Cached SPU unexpected read beat");
            for(integer i=0;i<8;i++) begin
                if(selected_burst[i*8+:8]!==memory.data[read_address+i])
                    $fatal(1,"Cached SPU burst byte mismatch address=%h got=%h expected=%h",read_address+i,selected_burst[i*8+:8],memory.data[read_address+i]);
                cached_byte_checks++;
            end
            read_address+=8;read_remaining--;
        end
        if(!native_memory && functional_remaining!=0) begin
            if(functional_wait>0) functional_wait--;
            else begin
                for(integer i=0;i<8;i++) functional_burst_data[i*8+:8]<=memory.data[functional_address+i];
                functional_burst_ready<=1;functional_remaining--;functional_address+=8;
            end
        end
    end
end
psx_vram_stream burst_port (
    .src_clk(clk2x),.mem_clk(mem_clk),.reset_async(transport_reset),.mem_initialized(initialized),
    .rd(native_memory && mem_RD && mem_request && !held && permit),
    .we(native_memory && mem_WE && mem_request && !held && permit),
    .addr(28'h1000000+{8'b0,mem_ADDR}),.count(mem_BURSTCNT),.be(drop_writes ? 8'b0:mem_BE),.din(mem_DIN),
    .busy(burst_busy),.fault(burst_fault),.ready(burst_ready),.dout(burst_data),
    .mem_valid(valid[1]),.mem_ready(ready[1]),.mem_write(write[1]),.mem_addr(addr[1]),
    .mem_wdata(data[1]),.mem_byte_en(byte_en[1]),.mem_rvalid(rvalid[1]),.mem_rdata(rdata));
