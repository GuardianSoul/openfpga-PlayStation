`timescale 1ns/1ps
module tb_psx_vram_stream;
    parameter integer BACKEND=1, MEM_HALF_NS=8, FIFO_AW=4, MEASURE_ONLY=0, TRACE=0;
    parameter real SRC_HALF_NS=7.381275, PIN_T_AC=5.5, PIN_T_AC_SECOND=5.5, PIN_T_HZ=6.0;
    reg src_clk=0, mem_clk=0, reset_async=1, source_enabled=1;
    reg clk_mem2=1;
    always #(SRC_HALF_NS) if(source_enabled) src_clk=~src_clk;
    always #(MEM_HALF_NS) mem_clk=~mem_clk;
    always #(MEM_HALF_NS/2) clk_mem2=~clk_mem2;
    reg rd=0, we=0;
    reg [27:0] addr=0;
    reg [7:0] count=0, be=0;
    reg [63:0] din=0;
    wire busy, fault, ready;
    wire [63:0] dout;
    wire mem_valid, mem_ready, mem_write, mem_rvalid, initialized;
    wire [23:0] mem_addr;
    wire [31:0] mem_wdata, mem_rdata;
    wire [3:0] mem_byte_en;
    psx_vram_stream #(.FIFO_AW(FIFO_AW)) dut (
        .src_clk(src_clk),.mem_clk(mem_clk),.reset_async(reset_async),.mem_initialized(initialized),
        .rd(rd),.we(we),.addr(addr),.count(count),.be(be),.din(din),
        .busy(busy),.fault(fault),.ready(ready),.dout(dout),
        .mem_valid(mem_valid),.mem_ready(mem_ready),.mem_write(mem_write),.mem_addr(mem_addr),
        .mem_wdata(mem_wdata),.mem_byte_en(mem_byte_en),.mem_rvalid(mem_rvalid),.mem_rdata(mem_rdata));
    wire rst=dut.mem_reset;

    function automatic integer slot(input [23:0] word_addr);
        case(word_addr[23:12])
            12'h000: slot=int'(word_addr[11:0]);
            12'h800: slot=4096+int'(word_addr[11:0]);
            12'h400: slot=8192+int'(word_addr[11:0]);
            12'hfff: slot=12288+int'(word_addr[11:0]);
            default: begin slot=0; $fatal(1,"Oracle address outside test regions %h",word_addr); end
        endcase
    endfunction
    function automatic [23:0] physical_word(input integer i);
        case(i/4096)
            0: physical_word=24'(i);
            1: physical_word=24'h800000+24'(i%4096);
            2: physical_word=24'h400000+24'(i%4096);
            3: physical_word=24'hfff000+24'(i%4096);
        endcase
    endfunction
    function automatic [7:0] initial_byte(input integer a);
        initial_byte=8'(a*17+(a>>8)*23+8'h5a);
    endfunction
    reg [7:0] expected[0:65535];
    initial for(integer i=0;i<16384;i=i+1)
        for(integer b=0;b<4;b=b+1) expected[i*4+b]=initial_byte(int'(physical_word(i))*4+b);

    generate if(BACKEND==0) begin: stalled_words
        reg [31:0] memory[0:16383], pipeline[0:4];
        reg [4:0] valid=0;
        integer cycle=0;
        initial for(integer i=0;i<16384;i=i+1)
            for(integer b=0;b<4;b=b+1) memory[i][b*8+:8]=initial_byte(int'(physical_word(i))*4+b);
        assign initialized=!rst;
        assign mem_ready=!rst && cycle%7!=0 && cycle%19!=0;
        assign mem_rvalid=valid[4];
        assign mem_rdata=pipeline[4];
        always @(posedge mem_clk) begin
            cycle<=cycle+1;
            if(rst) valid<=0;
            else begin
                valid<={valid[3:0],mem_valid && mem_ready && !mem_write};
                for(integer i=1;i<5;i=i+1) pipeline[i]<=pipeline[i-1];
                if(mem_valid && mem_ready) begin
                    if(mem_write) begin
                        for(integer b=0;b<4;b=b+1)
                            if(mem_byte_en[b]) memory[slot(mem_addr)][b*8+:8]<=mem_wdata[b*8+:8];
                    end else pipeline[0]<=memory[slot(mem_addr)];
                end
            end
        end
    end else begin: command_controller
        wire [1:0] ras_n,cas_n,we_n;
        wire [25:0] address;
        wire [3:0] bank,mask;
        wire cke,wren,rden,rvalid;
        wire [31:0] wdata,rdata;
        sdr_ctrl #(.T_INIT(16),.T_REFI(MEASURE_ONLY ? 480 : 24),.RL(5),.AHEAD(0)) controller (
            .clk(mem_clk),.rst(rst),.ready(initialized),.req_valid(mem_valid),.req_ready(mem_ready),
            .req_we(mem_write),.req_addr(mem_addr),.req_wdata(mem_wdata),.req_be(mem_byte_en),.req_tag(3'b0),
            .la_valid(1'b0),.la_addr(24'b0),.rd_valid(mem_rvalid),.rd_data(mem_rdata),.rd_tag(),.rd_next(),.rd_next_tag(),
            .dfi_ras_n(ras_n),.dfi_cas_n(cas_n),.dfi_we_n(we_n),.dfi_addr(address),.dfi_ba(bank),.dfi_cke(cke),
            .dfi_wrdata_en(wren),.dfi_wrdata(wdata),.dfi_wrdata_mask(mask),.dfi_rddata_en(rden),
            .dfi_rddata(rdata),.dfi_rddata_valid(rvalid));
        // The runner copies the existing strict DFI oracle, changing only its
        // second sparse region from 2 MiB to card slot 2's 32 MiB allocation.
        if(BACKEND==1) begin: dfi
            sdr_command_model memory (.clk(mem_clk),.rst(rst),.ras_n(ras_n),.cas_n(cas_n),.we_n(we_n),
                .address(address),.bank(bank),.cke(cke),.wrdata_en(wren),.wrdata(wdata),.wrdata_mask(mask),
                .rddata_en(rden),.rddata(rdata),.rddata_valid(rvalid));
        end else begin: pins
            reg [2:0] phy_reset=7;
            always @(posedge clk_mem2 or posedge reset_async)
                if(reset_async) phy_reset<=7;
                else phy_reset<={phy_reset[1:0],1'b0};
            wire [12:0] a;
            wire [1:0] ba,dm;
            wire ras,cas,we_pin,cke_pin,dram_clk;
            wire [15:0] dq;
            psx_sdr_phy phy (.clk(mem_clk),.rst(rst),.clk2x(clk_mem2),.rst2x(phy_reset[2]),.clk2x_90(!clk_mem2),
                .ras_n(ras_n),.cas_n(cas_n),.we_n(we_n),.addr(address),.ba(bank),.cke(cke),
                .wrdata_en(wren),.wrdata(wdata),.wrdata_mask(mask),.rddata_en(rden),
                .rddata(rdata),.rddata_valid(rvalid),.sdram_a(a),.sdram_ba(ba),.sdram_dm(dm),
                .sdram_dq(dq),.sdram_ras_n(ras),.sdram_cas_n(cas),.sdram_we_n(we_pin),
                .sdram_cke(cke_pin),.sdram_clock(dram_clk));
            sdr_pin_model #(.T_AC(PIN_T_AC),.T_AC_SECOND(PIN_T_AC_SECOND),.T_HZ(PIN_T_HZ)) memory (
                .clk(dram_clk),.cke(cke_pin),.ras_n(ras),.cas_n(cas),.we_n(we_pin),
                .address(a),.bank(ba),.dm(dm),.host_oe(phy.oe_reg),.dq(dq));
        end
    end endgenerate

    integer commands=0, comparisons=0, accepted_words=0, responses=0, max_outstanding=0;
    integer cmd_responses=0, issued_reads=0, consumed_reads=0;
    reg active=0, cmd_write=0, cmd_bad=0;
    reg [27:0] cmd_address;
    integer cmd_count;
    // An independent expected physical request stream survives posted writes.
    // The queue is built from accepted native inputs, not from DUT counters.
    reg [23:0] request_addr[0:131071];
    reg request_write[0:131071];
    reg [31:0] request_data[0:131071];
    reg [3:0] request_mask[0:131071];
    integer request_head=0, request_tail=0, held_cycles=0;
    wire legal_input=!(rd && we) && count!=0 && addr[2:0]==0 && addr[27:26]==0 &&
        ({1'b0,addr}+{18'b0,count,3'b0})<=29'h04000000;
    always @(posedge src_clk) if(!dut.src_reset && (rd || we) && !busy && legal_input) begin
        if(dut.cmd_full) $fatal(1,"Prepared native command had no FIFO space");
        for(integer i=0;i<2*int'(count);i=i+1) begin
            if(request_tail>=131072) $fatal(1,"Expected request stream exhausted");
            request_addr[request_tail]=24'(addr/4+i); request_write[request_tail]=we;
            request_data[request_tail]=(i%2) ? din[63:32] : din[31:0];
            request_mask[request_tail]=(i%2) ? be[7:4] : be[3:0];
            request_tail=request_tail+1;
        end
        if(we) for(integer i=0;i<int'(count);i=i+1) for(integer b=0;b<8;b=b+1)
            if(be[b]) expected[slot(24'(addr/4+i*2+b/4))*4+b%4]=din[b*8+:8];
    end
    always @(posedge src_clk) if(!dut.src_reset && (rd || we) && busy)
        held_cycles=held_cycles+1;
    always @(posedge mem_clk) if(!rst && mem_valid && mem_ready) begin
        if(request_head>=request_tail) $fatal(1,"Unexpected physical request");
        if(mem_write!==request_write[request_head] || mem_addr!==request_addr[request_head])
            $fatal(1,"Physical address/order mismatch got=%h index=%d",mem_addr,request_head);
        if(mem_write && (mem_wdata!==request_data[request_head] || mem_byte_en!==request_mask[request_head]))
            $fatal(1,"Physical write data/mask mismatch");
        request_head=request_head+1; accepted_words=accepted_words+1;
        if(!mem_write) issued_reads=issued_reads+1;
        if(issued_reads-2*consumed_reads>max_outstanding)
            max_outstanding=issued_reads-2*consumed_reads;
        if(issued_reads-2*consumed_reads>2*(1<<FIFO_AW)) $fatal(1,"Read credits exceeded storage");
    end
    always @(posedge src_clk) if(!dut.src_reset && ready) begin
        if(!active || cmd_write || cmd_responses>=cmd_count) $fatal(1,"Unexpected/duplicate native response");
        for(integer b=0;b<8;b=b+1) begin
            if(dout[b*8+:8]!== (cmd_bad ? 8'b0 : expected[slot(24'(cmd_address/4+cmd_responses*2+b/4))*4+b%4]))
                $fatal(1,"Native byte mismatch response=%d lane=%d got=%h",cmd_responses,b,dout);
            comparisons=comparisons+1;
        end
        cmd_responses=cmd_responses+1; responses=responses+1;
        if(!cmd_bad) consumed_reads=consumed_reads+1;
    end

    task automatic transaction(input [27:0] address_in,input integer beats,
        input bit write_in,input [7:0] mask_in,input [63:0] data_in,input bit invalid_in=0,input bit both=0,input bit no_wait=0);
        integer before_words;
        begin
            @(negedge src_clk); while(busy && !no_wait) @(negedge src_clk);
            active=1; cmd_address=address_in; cmd_count=beats; cmd_write=write_in && !both;
            cmd_bad=invalid_in; cmd_responses=0;
            before_words=accepted_words;
            addr=address_in; count=8'(beats); din=data_in; be=mask_in; rd=!write_in || both; we=write_in;
            // Inputs clear only after the accepting edge, as in gpu.vhd.
            @(posedge src_clk); while(busy) @(posedge src_clk);
            @(negedge src_clk); rd=0; we=0;
            while(busy || (!cmd_write && cmd_responses<beats)) @(negedge src_clk);
            if(!cmd_write && !invalid_in && request_head!=request_tail) $fatal(1,"Read fence lost physical words");
            if(invalid_in && (accepted_words!=before_words || !fault)) $fatal(1,"Invalid request touched memory");
            active=0; commands=commands+1;
        end
    endtask
    reg [31:0] rng=32'h50535871;
    function automatic [31:0] random_next(input [31:0] v);
        reg [31:0] x;
        begin x=v^(v<<13); x=x^(x>>17); random_next=x^(x<<5); end
    endfunction
    task automatic supply(input string pattern,input integer beats,input bit write_in,input bit scattered);
        realtime start_time,elapsed;
        reg [27:0] a;
        begin
            @(negedge src_clk); while(busy) @(negedge src_clk);
            start_time=$realtime;
            for(integer i=0;i<128;i=i+1) begin
                a=scattered ? 28'((i%8)*2048+((i*17)%64)*8) : 28'((i%8)*2048);
                transaction(a,beats,write_in,8'hff,64'hfe0180ff5544aa33^64'(i));
            end
            if(write_in) transaction(0,1,0,0,0); // drain posted writes; include fence time
            elapsed=$realtime-start_time;
            $display("STREAM_SUPPLY pattern=%s bytes=%0d elapsed_ns=%0.3f MBps=%0.3f",pattern,
                128*beats*8,elapsed,real'(128*beats*8)*1000.0/elapsed);
        end
    endtask
    // Reproduce gpu.vhd's clocked producer, including contiguous WE=1
    // packets. When BUSY=1 it holds both enable and payload, then advances
    // to the next packet exactly on acceptance, rather than dropping a pulse.
    task automatic queued_writes(input integer beats,input integer packets);
        integer produced;
        begin
            @(negedge src_clk); while(busy) @(negedge src_clk);
            produced=0;
            while(produced<packets) begin
                @(posedge src_clk);
                if(!busy) begin
                    we<=1; rd<=0; count<=8'(beats); be<=8'(produced);
                    addr<=28'((produced%8)*2048); din<={32'(produced),32'(~produced)};
                    produced=produced+1;
                end
            end
            @(posedge src_clk); while(busy) @(posedge src_clk);
            we<=0; rd<=0;
            @(negedge src_clk);
            commands=commands+packets;
            transaction(0,255,0,0,0);
            for(integer region=1;region<8;region=region+1)
                transaction(28'(region*2048),255,0,0,0);
            if(fault) $fatal(1,"Queued writer raised protocol fault");
        end
    endtask
    initial begin: exercise
        integer region,beats,offset,before_reset_words;
        reg [27:0] a;
        repeat(8) @(negedge src_clk); reset_async=0;
        if(MEASURE_ONLY) begin
            supply("read8_scattered",1,0,1);
            supply("read16_scattered",2,0,1);
            supply("scanout320_rgb555",80,0,0);
            supply("scanout320_rgb24",120,0,0);
            supply("write8_scattered",1,1,1);
            supply("write16_scattered",2,1,1);
            if(fault) $fatal(1,"Supply run raised protocol fault");
            $display("STREAM_SUPPLY PASS comparisons=%0d",comparisons); $finish;
        end
        transaction(0,1,0,0,0,0,0,1); // RD held during initialization
        for(integer m=0;m<256;m=m+1) begin
            rng=random_next(rng); region=m%4;
            beats=(m%13==0) ? 255 : 1+(m%37);
            offset=(m%3==0) ? 510 : (m%3==1) ? 1022 : 0;
            a={physical_word(region*4096+offset),2'b0};
            transaction(a,beats,1,8'(m),{rng,~rng});
            transaction(a,beats,0,0,0);
        end
        transaction(28'h3fffff8,1,1,8'hff,64'h0123456789abcdef);
        transaction(28'h3fffff8,1,0,0,0);
        queued_writes(1,128);
        queued_writes(2,128);
        queued_writes(255,32);
        if(held_cycles==0) $fatal(1,"Clocked producer never held a stalled request");
        if(fault) $fatal(1,"Good requests raised fault");
        transaction(28'h4000000,3,0,0,0,1);
        transaction(28'h3fffff8,2,1,8'hff,0,1);
        transaction(28'h0000001,1,0,0,0,1);
        transaction(28'h0000000,0,1,8'hff,0,1);
        transaction(28'h0000000,1,1,8'hff,0,1,1);
        // Twice: the first command pointer may happen to be zero modulo the
        // small FIFO; the second reset exercises a nonzero pointer as well.
        for(integer reset_case=0;reset_case<2;reset_case=reset_case+1) begin
        // Abort a partially returned read with the source clock stopped.
        @(negedge src_clk); while(busy) @(negedge src_clk);
        active=1; cmd_bad=0; cmd_write=0; cmd_address=0; cmd_count=255; cmd_responses=0;
        addr=0; count=255; rd=1;
        @(posedge src_clk); while(busy) @(posedge src_clk);
        @(negedge src_clk); rd=0;
        repeat(20) @(negedge src_clk);
        source_enabled=0;
        repeat(100) @(negedge mem_clk);
        if(!busy) $fatal(1,"Paused source ended a read early");
        reset_async=1;
        repeat(8) @(negedge mem_clk);
        active=0; issued_reads=0; consumed_reads=0; request_head=request_tail;
        before_reset_words=accepted_words;
        reset_async=0;
        repeat(200) @(negedge mem_clk); // source still stopped after reset release
        if(accepted_words!=before_reset_words) $fatal(1,"Stopped source reset replayed stale command");
        source_enabled=1;
        repeat(8) @(negedge src_clk);
        transaction(0,255,0,0,0);
        if(fault) $fatal(1,"Reset did not clear protocol fault");
        end
        $display("VRAM_STREAM PASS backend=%0d src_half=%g mem_half=%0d fifo=%0d commands=%0d words=%0d comparisons=%0d max_reserved_or_output_words=%0d held_cycles=%0d",
            BACKEND,SRC_HALF_NS,MEM_HALF_NS,1<<FIFO_AW,commands,accepted_words,comparisons,max_outstanding,held_cycles);
        $finish;
    end
    integer trace_file;
    reg [8191:0] trace_path;
    initial if(TRACE) begin
        if(!$value$plusargs("trace=%s",trace_path)) $fatal(1,"Trace path required");
        trace_file=$fopen(trace_path,"w");
    end
    always @(negedge src_clk) if(TRACE)
        $fwrite(trace_file,"%0t %b %b %b %016x\n",$time,busy,fault,ready,ready?dout:64'b0);
    always @(negedge mem_clk) if(TRACE)
        $fwrite(trace_file,"MEM %0t %b %b %b %06x %08x %x\n",
            $time,mem_valid,mem_ready,mem_write,mem_addr,mem_wdata,mem_byte_en);
    initial begin #100000000; $fatal(1,"VRAM stream watchdog"); end
endmodule
