// Appended to the established paired-controller/PHY/pin timing fixture.
    reg [3:0] p_valid=0,p_write=0;
    wire [3:0] p_ready,p_rsp_valid,p_rsp_error;
    reg [3:0][24:0] p_addr=0;
    reg [3:0][3:0] p_halfwords=0;
    reg [3:0][127:0] p_wdata=0;
    reg [3:0][15:0] p_byte_en=0;
    wire [3:0][127:0] p_rsp_rdata;
    reg [3:0] hold_rsp=0;
    integer ticks=0,requests=0,responses=0,compares=0,boot_epochs=0;
    integer completed[0:3];
    wire [3:0] p_rsp_ready={4{ticks%17<9}} & ~hold_rsp;
    reg boot_request=0,guest_quiesced=0;
    reg hold_pin_idle=0;
    reg error_case=0,inject_wait_error=0;
    wire packet_pin_idle=idle && !hold_pin_idle;
    wire boot_owned,fabric_idle,protocol_error;
    psx_main_memory native (
        .clk(clk),.reset(rst),.initialized(initialized),.boot_request(boot_request),
        .guest_quiesced(guest_quiesced),.boot_owned(boot_owned),.idle(fabric_idle),
        .protocol_error(protocol_error),.p_valid(p_valid),.p_write(p_write),.p_ready(p_ready),
        .p_addr(p_addr),.p_halfwords(p_halfwords),.p_wdata(p_wdata),.p_byte_en(p_byte_en),
        .p_rsp_valid(p_rsp_valid),.p_rsp_ready(p_rsp_ready),.p_rsp_rdata(p_rsp_rdata),
        .p_rsp_error(p_rsp_error),.z_valid(valid),.z_we(write),.z_die(die),.z_last(last),
        .z_ready(ready),.z_rvalid(rvalid),.z_idle(packet_pin_idle),.wait_error(wait_error || inject_wait_error),
        .z_addr(address),.z_wdata(data),.z_wmask(mask),.z_rdata(read_data));

    function automatic integer slot(input integer word_address);
        if(word_address<4096) slot=word_address;
        else if(word_address>='h3ff000 && word_address<'h401000) slot=4096+word_address-'h3ff000;
        else if(word_address>='h7ff000 && word_address<'h800000) slot=12288+word_address-'h7ff000;
        else if(word_address>='h420000 && word_address<'h421000) slot=16384+word_address-'h420000;
        else if(word_address>='h440000 && word_address<'h441000) slot=20480+word_address-'h440000;
        else if(word_address>='h020000 && word_address<'h021000) slot=24576+word_address-'h020000;
        else if(word_address>='h040000 && word_address<'h041000) slot=28672+word_address-'h040000;
        else begin slot=0;$fatal(1,"Packet oracle address outside selected windows");end
    endfunction
    function automatic integer window_word(input integer index);
        if(index<4096) window_word=index;
        else if(index<12288) window_word='h3ff000+index-4096;
        else if(index<16384) window_word='h7ff000+index-12288;
        else if(index<20480) window_word='h420000+index-16384;
        else if(index<24576) window_word='h440000+index-20480;
        else if(index<28672) window_word='h020000+index-24576;
        else window_word='h040000+index-28672;
    endfunction
    reg [7:0] expected[0:131071];
    reg [3:0] owed=0,expected_error=0;
    reg [127:0] expected_response[0:3];
    integer accepted[0:3],abandoned=0;
    initial begin
        for(integer i=0;i<32768;i++)
            for(integer b=0;b<4;b++) expected[4*i+b]=8'((window_word(i)*4+b)*17+((window_word(i)*4+b)>>8)*23+'h5a);
        for(integer p=0;p<4;p++) begin completed[p]=0;accepted[p]=0;expected_response[p]=0;end
    end
    always @(posedge clk) begin
        ticks<=ticks+1;
        if(rst) begin
            owed=0;expected_error=0;
            for(integer p=0;p<4;p++) expected_response[p]=0;
        end
        else begin
            if(protocol_error && !error_case) $fatal(1,"Main packet protocol error");
            if(!initialized && (p_ready!=0 || valid)) $fatal(1,"Packet accepted before chip configuration");
            if(boot_owned && !initialized) $fatal(1,"BIOS ownership before chip configuration");
            if(boot_owned && (p_ready[2:0]!=0 || owed[2:0]!=0)) $fatal(1,"BIOS ownership before guest drain");
            if(!boot_owned && p_ready[3]) $fatal(1,"BIOS accepted without ownership");
            // Check the endpoint completion before buffering it per client;
            // another packet may be active when a delayed client consumes it.
            if(native.rsp_valid && (!packet_pin_idle || valid || rvalid))
                $fatal(1,"Packet response before physical drain");
            for(integer p=0;p<4;p++) begin
                if(p_rsp_valid[p] && p_rsp_ready[p]) begin
                    if(!owed[p] || p_rsp_error[p]!==expected_error[p] ||
                        p_rsp_rdata[p]!==expected_response[p])
                        $fatal(1,"Packet byte/response mismatch port=%0d got=%h want=%h err=%b/%b",p,p_rsp_rdata[p],expected_response[p],p_rsp_error[p],expected_error[p]);
                    completed[p]++;responses++;compares+=16;owed[p]=0;
                end
                if(p_valid[p] && p_ready[p]) begin
                    if(owed[p]) $fatal(1,"Duplicate packet owner");
                    owed[p]=1;requests++;accepted[p]++;
                    expected_response[p]=0;
                    expected_error[p]=p_halfwords[p]<1 || p_halfwords[p]>8 ||
                        integer'(p_addr[p])*2+integer'(p_halfwords[p])*2>33554432 || error_case;
                    if(!expected_error[p]) begin
                        for(integer b=0;b<integer'(p_halfwords[p])*2;b++) begin
                            integer byte_address,index;
                            byte_address=integer'(p_addr[p])*2+b;
                            index=4*slot(byte_address/4)+byte_address%4;
                            if(p_write[p]) begin
                                if((p_byte_en[p]>>b)&1) expected[index]=8'(p_wdata[p]>>(b*8));
                            end else expected_response[p][b*8+:8]=expected[index];
                        end
                    end
                end
            end
        end
        if(ticks>1500000) $fatal(1,"Main memory timeout");
    end
    task automatic send(input integer p,input [24:0] a,input [3:0] n,
        input bit wr,input [127:0] d,input [15:0] bm);
        integer before_count;
        before_count=completed[p];
        @(negedge clk);p_valid[p]=1;p_addr[p]=a;p_halfwords[p]=n;
        p_write[p]=wr;p_wdata[p]=d;p_byte_en[p]=bm;
        @(posedge clk);while(!p_ready[p]) @(posedge clk);
        @(negedge clk);p_valid[p]=0;
        p_addr[p]='1;p_halfwords[p]=0;p_wdata[p]=~d;p_byte_en[p]=~bm;p_write[p]=~wr;
        while(completed[p]==before_count) @(negedge clk);
    endtask
    task automatic check_chip_bytes;
        for(integer i=0;i<32768;i++) begin
            if({chip0.memory[2*i+1],chip0.memory[2*i],chip1.memory[2*i+1],chip1.memory[2*i]} !==
                {expected[4*i+3],expected[4*i+2],expected[4*i+1],expected[4*i]})
                $fatal(1,"Physical PSRAM bytes differ word=%h",window_word(i));
            compares+=4;
        end
    endtask
    task automatic init_complete;
        wait(initialized);
        if(chip0.bcr[0]!=16'h641f || chip0.bcr[1]!=16'h641f ||
           chip1.bcr[0]!=16'h641f || chip1.bcr[1]!=16'h641f ||
           chip0.rcr[0]!=16'h0010 || chip0.rcr[1]!=16'h0010 ||
           chip1.rcr[0]!=16'h0010 || chip1.rcr[1]!=16'h0010)
            $fatal(1,"Main PSRAM die configuration");
    endtask
    task automatic enter_boot;
        @(negedge clk);guest_quiesced=1;boot_request=1;
        while(!boot_owned) @(negedge clk);
        boot_epochs++;
    endtask
    task automatic exit_boot;
        @(negedge clk);boot_request=0;
        while(boot_owned) @(negedge clk);
        guest_quiesced=0;
    endtask
    localparam [127:0] DATA=128'hfedcba98765432100123456789abcdef;
    initial begin
        repeat(4) @(negedge clk);rst=0;
        if($test$plusargs("ERROR_TEST")) begin
            error_case=1;enter_boot();init_complete();
            fork
                send(3,25'h840000,8,0,0,0);
                begin wait(owed[3]);@(negedge clk);inject_wait_error=1;end
            join
            if(!protocol_error || requests!=1 || responses!=1 || completed[3]!=1)
                $fatal(1,"Backend error coverage missing");
            check_chip_bytes();
            $display("PASS MAIN MEMORY backend error propagated with zero response data");
            $finish;#1;$fatal(1,"Simulation continued after finish");
        end
        // Queue the first BIOS instruction before configuration, including the
        // ownership request. Neither acknowledgement may use PLL lock alone.
        fork
            enter_boot();
            send(3,25'h840000,2,1,128'h0000000000000000000000003c1a8001,16'h000f);
        join
        init_complete();exit_boot();
        // Guest reads the uploaded instruction. Main-RAM locations at the
        // matching die-0 offset must retain their independent initial bytes.
        send(0,25'h840000,2,0,0,0);
        send(0,25'h040000,8,0,0,0);
        send(0,25'h080000,8,0,0,0);
        // Native request held over actual 192 us chip initialization.
        send(0,25'd0,8,0,0,0);init_complete();
        for(integer die_index=0;die_index<2;die_index++) begin
            send(0,25'(die_index*'h800000+64),8,0,0,0);
            for(integer n=1;n<=8;n++) for(integer align_bit=0;align_bit<2;align_bit++) begin
                reg [24:0] a;
                a=25'(die_index*'h800000+64+align_bit);
                send(0,a,4'(n),1,DATA^128'(n^(die_index*'h7359)),16'hffff);
                send(0,a,4'(n),0,0,0);
            end
        end
        send(0,25'd64,8,0,0,0); // the other die's writes must not alias this
        // Every low-eight-bit mask and all high lane combinations, overlap.
        for(integer bm=0;bm<256;bm++) begin
            send(0,25'd255,8,1,DATA^128'(bm),{8'(bm^'ha5),8'(bm)});
            send(0,25'd254,8,0,0,0);
            send(0,25'd262,2,0,0,0);
        end
        send(0,25'h7fffff,8,1,DATA,16'hffff); // cross die 0 -> die 1
        send(0,25'h7fffff,8,0,0,0);
        send(0,25'hffffff,1,1,DATA,16'hffff);
        send(0,25'hffffff,1,0,0,0);
        begin
            integer traffic;
            traffic=chip0.reads+chip0.writes;
            send(0,25'hffffff,2,1,DATA,16'hffff);
            send(0,25'h1000000,1,0,0,0);
            send(0,25'd0,0,1,DATA,16'hffff);
            send(0,25'd0,9,0,0,0);
            if(traffic!=chip0.reads+chip0.writes) $fatal(1,"Invalid packet generated chip traffic");
        end
        // Three independent guest clients contend on the same physical pins.
        fork
            begin for(integer i=0;i<80;i++) begin
                send(0,25'(1024+i*9),8,1,DATA^128'(i),16'hffff);
                send(0,25'(1024+i*9),8,0,0,0);
            end end
            begin for(integer i=0;i<80;i++) begin
                send(1,25'('h800200+i*3),2,1,DATA^128'(i*17),16'h000f);
                send(1,25'('h800200+i*3),2,0,0,0);
            end end
            begin for(integer i=0;i<80;i++) begin
                send(2,25'('h880100+i*9),8,1,DATA^128'(i*29),16'hffff);
                send(2,25'('h880100+i*9),8,0,0,0);
            end end
        join
        // A conservatively held backend-idle indication and an offered but
        // unaccepted guest request must both fence a queued BIOS request.
        hold_pin_idle=1;
        fork
            send(0,25'd500,8,1,DATA,16'hffff);
            begin wait(owed[0]);send(2,25'h880100,8,0,0,0);end
            begin
                wait(p_valid[2]);@(negedge clk);boot_request=1;guest_quiesced=1;
                wait(idle && native.packet.sent!=0);
                repeat(10) @(negedge clk);
                if(boot_owned || p_ready[2] || p_ready[3]) $fatal(1,"Offered guest/pin work bypassed drain");
                hold_pin_idle=0;
            end
            begin wait(boot_request);send(3,25'h840020,2,1,DATA,16'h000f);end
        join
        if(!boot_owned) $fatal(1,"Queued BIOS packet failed ownership");
        boot_epochs++;exit_boot();
        // A guest response held after pin completion blocks BIOS ownership.
        hold_rsp[1]=1;
        fork
            send(1,25'h800020,8,1,DATA,16'hffff);
            begin
                wait(owed[1]);@(negedge clk);boot_request=1;guest_quiesced=1;
                wait(p_rsp_valid[1]);repeat(20) @(negedge clk);
                if(boot_owned || p_ready[3]) $fatal(1,"Unconsumed guest response bypassed drain");
                hold_rsp[1]=0;
            end
        join
        wait(boot_owned);boot_epochs++;
        for(integer i=0;i<32;i++) send(3,25'('h840000+i*2),2,1,DATA^128'(i),16'h000f);
        send(3,25'h840000,8,0,0,0);
        // BIOS's held response also blocks ownership release.
        hold_rsp[3]=1;
        fork
            send(3,25'h840007,8,1,DATA,16'hffff);
            begin
                wait(owed[3]);@(negedge clk);boot_request=0;
                wait(p_rsp_valid[3]);repeat(20) @(negedge clk);
                if(!boot_owned || p_ready[0]) $fatal(1,"Unconsumed BIOS response bypassed release");
                hold_rsp[3]=0;
            end
        join
        wait(!boot_owned);guest_quiesced=0;
        send(0,25'd1024,8,0,0,0);check_chip_bytes();
        // Reset discards an in-flight read, preserves already written bytes.
        @(negedge clk);p_valid[0]=1;p_addr[0]=25'd255;p_halfwords[0]=8;p_write[0]=0;
        @(posedge clk);while(!p_ready[0]) @(posedge clk);
        @(negedge clk);p_valid[0]=0;
        wait(valid && !ready);@(negedge clk);rst=1;abandoned++;
        repeat(12) @(negedge clk);rst=0;init_complete();
        send(0,25'd255,8,0,0,0);check_chip_bytes();
        // A pending zero-mask write safely exercises the reset's write drain.
        @(negedge clk);p_valid[0]=1;p_addr[0]=25'd300;p_halfwords[0]=8;
        p_write[0]=1;p_wdata[0]=DATA;p_byte_en[0]=0;
        @(posedge clk);while(!p_ready[0]) @(posedge clk);
        @(negedge clk);p_valid[0]=0;
        wait(valid && !ready);@(negedge clk);rst=1;abandoned++;
        repeat(12) @(negedge clk);rst=0;init_complete();
        enter_boot();send(3,25'h840000,8,0,0,0);exit_boot();check_chip_bytes();
        if(requests!=responses+abandoned || boot_epochs!=4 || accepted[1]<160 || accepted[2]<160)
            $fatal(1,"Main memory coverage missing");
        $display("PASS MAIN MEMORY requests=%0d responses=%0d abandoned=%0d compares=%0d boot_epochs=%0d ticks=%0d physical_reads=%0d physical_writes=%0d",requests,responses,abandoned,compares,boot_epochs,ticks,chip0.reads,chip0.writes);
        $finish;
    end
endmodule
