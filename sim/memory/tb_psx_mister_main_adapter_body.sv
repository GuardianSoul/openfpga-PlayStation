    parameter real CORE_HALF=15.0;
    reg core_clock_run=1,clk1x=0,clk3x=0,core_ce=1,discard_returns=0;
    always #(CORE_HALF) if(core_clock_run) clk1x=~clk1x;
    always #(CORE_HALF/3.0) clk3x=~clk3x;
    reg ram_ena=0,ram_rnw=1,ram_dma=0,ram_cache=0;
    reg [24:0] ram_adr=0;
    reg [1:0] ram_cntDMA=0;
    reg [31:0] ram_dataWrite=0;
    reg [3:0] ram_be=0;
    wire ram_done,dma_wr,dma_reqprocessed,source_idle,service_idle,adapter_fault;
    wire [31:0] ram_dataRead32,dma_data,cache_data;
    wire [7:0] cache_addr;
    wire [3:0] cache_wr;
    reg [22:0] fifo_address=0;
    reg [31:0] fifo_data=0;
    reg fifo_empty=1;
    wire fifo_read;
    wire av,aw,ar,arv,are,arr;
    wire [24:0] aa;
    wire [3:0] an;
    wire [127:0] ad,ard;
    wire [15:0] ab;
    psx_mister_main_adapter adapter (
        .clk1x(clk1x),.clk3x(clk3x),.mem_clk(clk),.reset_async(rst),
        .core_ce(core_ce),.discard_returns(discard_returns),
        .ram_ena(ram_ena),.ram_rnw(ram_rnw),.ram_dma(ram_dma),.ram_cache(ram_cache),
        .ram_adr(ram_adr),.ram_cntDMA(ram_cntDMA),.ram_dataWrite(ram_dataWrite),.ram_be(ram_be),
        .ram_done(ram_done),.ram_dataRead32(ram_dataRead32),.dma_wr(dma_wr),.dma_data(dma_data),
        .dma_reqprocessed(dma_reqprocessed),.cache_wr(cache_wr),.cache_data(cache_data),.cache_addr(cache_addr),
        .ram_dmafifo_adr(fifo_address),.ram_dmafifo_data(fifo_data),
        .ram_dmafifo_empty(fifo_empty),.ram_dmafifo_read(fifo_read),
        .source_idle(source_idle),.service_idle(service_idle),.fault(adapter_fault),
        .p_valid(av),.p_write(aw),.p_ready(ar),.p_addr(aa),.p_halfwords(an),
        .p_wdata(ad),.p_byte_en(ab),.p_rsp_valid(arv),.p_rsp_error(are),.p_rsp_rdata(ard),.p_rsp_ready(arr));
    wire [3:0] p_ready,p_rsp_valid,p_rsp_error;
    wire [3:0][127:0] p_rsp_data;
    wire boot_owned,fabric_idle,protocol_error;
    psx_main_memory native (
        .clk(clk),.reset(rst),.initialized(initialized),.boot_request(1'b0),.guest_quiesced(1'b0),
        .boot_owned(boot_owned),.idle(fabric_idle),.protocol_error(protocol_error),
        .p_valid({3'b0,av}),.p_write({3'b0,aw}),.p_ready(p_ready),
        .p_addr({75'b0,aa}),.p_halfwords({12'b0,an}),.p_wdata({384'b0,ad}),.p_byte_en({48'b0,ab}),
        .p_rsp_valid(p_rsp_valid),.p_rsp_ready({3'b0,arr}),.p_rsp_rdata(p_rsp_data),.p_rsp_error(p_rsp_error),
        .z_valid(valid),.z_we(write),.z_die(die),.z_last(last),.z_ready(ready),.z_rvalid(rvalid),
        .z_idle(idle),.wait_error(wait_error),.z_addr(address),.z_wdata(data),.z_wmask(mask),.z_rdata(read_data));
    assign ar=p_ready[0];assign arv=p_rsp_valid[0];assign are=p_rsp_error[0];assign ard=p_rsp_data[0];
    integer count=0,front=0,completed=0,cache_events=0,return_events=0,acks=0,popped=0;
    integer vector_file,expected_file,rc,ix,bits,addr_expected;
    reg [31:0] word_expected;
    reg [7:0] kind;
    reg [24:0] v_addr[0:4095];
    integer v_read[0:4095],v_cache[0:4095],v_dma[0:4095],v_count[0:4095];
    reg [31:0] v_data[0:4095];
    reg [3:0] v_be[0:4095];
    integer want_returns[0:4095],seen_returns[0:4095],want_cache[0:4095],seen_cache[0:4095];
    reg [127:0] return_words[0:4095],cache_words[0:4095];
    reg [15:0] cache_masks[0:4095];
    reg [7:0] cache_addresses[0:4095];
    reg monitor=1,allow_fault=0;
    initial begin
        for(integer i=0;i<4096;i++) begin
            want_returns[i]=0;seen_returns[i]=0;want_cache[i]=0;seen_cache[i]=0;
            return_words[i]=0;cache_words[i]=0;cache_masks[i]=0;cache_addresses[i]=0;
        end
        vector_file=$fopen("vectors.txt","r");expected_file=$fopen("expected.txt","r");
        if(!vector_file || !expected_file) $fatal(1,"Missing adapter fixture");
        while(!$feof(vector_file)) begin
            rc=$fscanf(vector_file,"%h %h %h %h %h %h %h\n",v_addr[count],v_read[count],v_cache[count],v_dma[count],v_count[count],v_data[count],v_be[count]);
            if(rc!=7) $fatal(1,"Bad adapter input row");count++;
        end
        while(!$feof(expected_file)) begin
            rc=$fscanf(expected_file,"%h %s %h %h %h\n",ix,kind,bits,addr_expected,word_expected);
            if(rc!=5 || ix>=count) $fatal(1,"Bad upstream event row");
            if(kind=="C") begin
                cache_words[ix][want_cache[ix]*32+:32]=word_expected;
                cache_masks[ix][want_cache[ix]*4+:4]=4'(bits);cache_addresses[ix]=8'(addr_expected);want_cache[ix]++;
            end else begin return_words[ix][want_returns[ix]*32+:32]=word_expected;want_returns[ix]++;end
        end
        $fclose(vector_file);$fclose(expected_file);
    end
    integer physical_packets=0;
    always @(posedge clk) if(!rst) begin
        if(av && ar) physical_packets++;
        if(protocol_error || wait_error) $fatal(1,"Native main backend fault");
    end
    always @(posedge clk3x) if(!rst) begin
        if(adapter_fault && !allow_fault) $fatal(1,"Native adapter fault");
        if(monitor && cache_wr!=0) begin
            if(seen_cache[front]>=want_cache[front] || cache_addr!==cache_addresses[front] ||
                cache_wr!==cache_masks[front][seen_cache[front]*4+:4] ||
                cache_data!==cache_words[front][seen_cache[front]*32+:32])
                $fatal(1,"Native cache event mismatch vector=%0d mask=%h addr=%h data=%h",front,cache_wr,cache_addr,cache_data);
            seen_cache[front]++;cache_events++;
        end
        if(fifo_read) popped++;
    end
    always @(posedge clk1x) if(!rst) begin
        if(dma_reqprocessed) acks++;
        if(monitor && (ram_done || dma_wr)) begin
            if(ram_done && dma_wr || seen_returns[front]>=want_returns[front] ||
                (dma_wr ? dma_data:ram_dataRead32)!==return_words[front][seen_returns[front]*32+:32])
                $fatal(1,"Native return event mismatch vector=%0d beat=%0d got=%h want=%h",front,seen_returns[front],dma_wr ? dma_data:ram_dataRead32,return_words[front][seen_returns[front]*32+:32]);
            if(v_dma[front]!=dma_wr || seen_cache[front]!=want_cache[front])
                $fatal(1,"Native return before cache consumption or wrong return class");
            seen_returns[front]++;return_events++;
            if(seen_returns[front]==want_returns[front]) begin completed++;front++;end
        end
        if(!core_ce && (ram_done || dma_wr || dma_reqprocessed)) $fatal(1,"Native return during CE pause");
    end
    task automatic pulse(input integer i);
        @(negedge clk1x);
        ram_ena=1;ram_adr=v_addr[i];ram_rnw=v_read[i];ram_cache=v_cache[i];ram_dma=v_dma[i];
        ram_cntDMA=2'(v_count[i]);ram_dataWrite=v_data[i];ram_be=v_be[i];
        @(negedge clk1x);ram_ena=0;
        ram_adr='1;ram_rnw=0;ram_cache=0;ram_dma=0;ram_dataWrite='1;ram_be=0;
    endtask
    task automatic drain;
        wait(source_idle && service_idle && fabric_idle);
        repeat(5) @(negedge clk1x);
    endtask
    integer extra_start,old_completed,old_packets;
    initial begin
        #1;if(count<2000) $fatal(1,"Incomplete vectors");
        repeat(4) @(negedge clk);rst=0;
        wait(!adapter.reset1[2] && !adapter.reset3[2]);
        // The first actual upstream-style write queues before chip initialization.
        pulse(0);wait(initialized);drain();
        if(completed!=1) $fatal(1,"Startup queued request was lost");
        for(integer i=1;i<count;i++) begin
            pulse(i);drain();
            if(front!=i+1) $fatal(1,"Native missing return vector=%0d",i);
        end
        // Pipeline a second DMA read before the first stream has returned.
        front=count-2;seen_returns[front]=0;seen_returns[front+1]=0;
        old_completed=completed;extra_start=acks;
        pulse(count-2);wait(acks>extra_start);
        if(completed!=old_completed) $fatal(1,"DMA request acknowledgement was not early");
        pulse(count-1);
        @(negedge clk1x);core_ce=0;
        repeat(40) @(negedge clk1x);
        if(service_idle) $fatal(1,"Service idle with held DMA return");
        core_ce=1;drain();
        if(completed!=old_completed+2 || acks!=extra_start+2) $fatal(1,"Queued DMA read lost");
        // Stop CE before the 3x acceptance token reaches 1x. Its early ACK
        // must remain pending until the upstream DMA can actually consume it.
        front=count-2;seen_returns[front]=0;old_completed=completed;extra_start=acks;
        pulse(count-2);core_ce=0;
        repeat(40) @(negedge clk1x);
        if(acks!=extra_start || service_idle) $fatal(1,"DMA ACK escaped paused consumer");
        core_ce=1;drain();
        if(completed!=old_completed+1 || acks!=extra_start+1) $fatal(1,"DMA ACK lost across CE pause");
        // A stopped consumer clock holds its return; it cannot claim drain.
        front=count-2;seen_returns[front]=0;old_completed=completed;
        pulse(count-2);
        @(negedge clk1x);core_clock_run=0;
        repeat(1000) @(negedge clk);
        if(service_idle) $fatal(1,"Drain while source clock stopped");
        core_clock_run=1;drain();
        if(completed!=old_completed+1) $fatal(1,"Stopped-clock return lost");
        // Only a separately reset consumer may discard returns. Service keeps
        // running with CE low and drains the queued physical transaction.
        monitor=0;pulse(count-2);
        @(negedge clk1x);core_ce=0;discard_returns=1;
        drain();discard_returns=0;core_ce=1;monitor=1;
        // A posted write accepted at 3x must reach the chip before a later read.
        front=count;want_returns[front]=1;v_dma[front]=0;return_words[front]=128'h76543210;
        v_addr[front]=25'h2000;v_read[front]=1;v_cache[front]=0;v_count[front]=0;
        @(negedge clk3x);fifo_empty=0;fifo_address=23'h2000;fifo_data=32'h76543210;
        fork
            begin wait(fifo_read);@(posedge clk3x);@(negedge clk3x);fifo_empty=1;end
            pulse(count);
        join
        drain();if(popped!=1 || front!=count+1) $fatal(1,"Posted FIFO ordering/pop failed");
        // Common reset abandons a read, reinitializes both chips, and preserves
        // previously committed posted data. No nonzero partial write is reset.
        monitor=0;pulse(count-2);wait(valid && !write);
        @(negedge clk);rst=1;ram_ena=0;
        repeat(8) @(negedge clk);rst=0;wait(initialized);drain();
        front=count;seen_returns[front]=0;monitor=1;pulse(count);drain();
        // Reject an unsupported address without physical traffic and expose fault.
        front=count+1;want_returns[front]=1;v_dma[front]=0;return_words[front]=0;
        v_addr[front]=25'h1000000;v_read[front]=1;v_cache[front]=0;v_count[front]=0;
        allow_fault=1;old_packets=physical_packets;pulse(count+1);drain();
        if(!adapter_fault || physical_packets!=old_packets) $fatal(1,"Invalid address not rejected");
        $display("PASS MISTER MAIN ADAPTER vectors=%0d returns=%0d cache=%0d DMAacks=%0d posted=%0d physical_packets=%0d",count,return_events,cache_events,acks,popped,physical_packets);
        $finish;
    end
    initial begin #20000000;$fatal(1,"Native main adapter timeout");end
endmodule
