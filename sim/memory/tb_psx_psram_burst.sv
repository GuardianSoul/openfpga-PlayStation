`timescale 1ns/1ps
module tb_psx_psram_burst;
    parameter integer T_INIT=12000, ORIGINAL_PHY=0, MEASURE_ONLY=0, TRACE=0;
    parameter real CLOCK_DELAY=0.0, CONTROL_DELAY=0.0, RETURN_DELAY=0.0, DATA_ACCESS=9.0;
    reg clk=0, clk2x=1, rst=1;
    always #8 clk=~clk;
    always #4 clk2x=~clk2x;
    wire clk_sample;
    assign #1.4 clk_sample=clk2x;
    reg valid=0,write=0,last=0,die=0;
    reg [21:0] address=0;
    reg [31:0] data=0;
    reg [3:0] mask=0;
    wire ready,rvalid,idle,initialized,wait_error;
    wire [31:0] read_data;
    wire [5:0] o_a;
    wire [31:0] o_dq, i_dq;
    wire o_dq_oe,o_adv_n,o_oe_n,o_we_n,o_cre,o_clk_en;
    wire [1:0] o_ce_n,i_wait;
    wire [3:0] o_bm_n;
    psx_psram_burst #(.T_INIT(T_INIT)) dut (
        .clk(clk),.rst(rst),.z_valid(valid),.z_die(die),.z_ready(ready),.z_we(write),
        .z_addr(address),.z_wdata(data),.z_wmask(mask),.z_last(last),.z_rvalid(rvalid),.z_rdata(read_data),.z_idle(idle),
        .x_valid(1'b0),.x_we(1'b0),.x_addr(22'b0),.x_wdata(32'b0),.x_be(4'b0),.x_ack(),.x_rdata(),
        .cfg(4'h5),.cfg_wr(1'b0),.ready(initialized),.wait_err(wait_error),.o_a(o_a),.o_dq(o_dq),
        .o_dq_oe(o_dq_oe),.o_adv_n(o_adv_n),.o_oe_n(o_oe_n),.o_we_n(o_we_n),.o_cre(o_cre),
        .o_ce_n(o_ce_n),.o_bm_n(o_bm_n),.o_clk_en(o_clk_en),.i_dq(i_dq),.i_wait(i_wait));
    wire [21:16] a0,a1;
    wire [15:0] dq0,dq1;
    wire c0clk,c1clk,adv0,adv1,cre0,cre1,ce00,ce01,ce10,ce11,oe0,oe1,we0,we1,ub0,ub1,lb0,lb1,wait0,wait1;
    generate if(ORIGINAL_PHY) begin: original
        psram_phy pads (
            .clk(clk),.clk2x(clk2x),.o_a(o_a),.o_dq(o_dq),.o_dq_oe(o_dq_oe),.o_adv_n(o_adv_n),
            .o_oe_n(o_oe_n),.o_we_n(o_we_n),.o_cre(o_cre),.o_ce_n(o_ce_n),.o_bm_n(o_bm_n),.clk_en(o_clk_en),
            .i_dq(i_dq),.i_wait(i_wait),.cram0_a(a0),.cram1_a(a1),.cram0_dq(dq0),.cram1_dq(dq1),
            .cram0_wait(wait0),.cram1_wait(wait1),.cram0_clk(c0clk),.cram1_clk(c1clk),.cram0_adv_n(adv0),.cram1_adv_n(adv1),
            .cram0_cre(cre0),.cram1_cre(cre1),.cram0_ce0_n(ce00),.cram0_ce1_n(ce01),.cram1_ce0_n(ce10),.cram1_ce1_n(ce11),
            .cram0_oe_n(oe0),.cram1_oe_n(oe1),.cram0_we_n(we0),.cram1_we_n(we1),
            .cram0_ub_n(ub0),.cram0_lb_n(lb0),.cram1_ub_n(ub1),.cram1_lb_n(lb1));
    end else begin: retimed
        psx_psram_burst_phy pads (
            .clk(clk),.clk2x(clk2x),.clk_sample(clk_sample),.o_a(o_a),.o_dq(o_dq),.o_dq_oe(o_dq_oe),.o_adv_n(o_adv_n),
            .o_oe_n(o_oe_n),.o_we_n(o_we_n),.o_cre(o_cre),.o_ce_n(o_ce_n),.o_bm_n(o_bm_n),.clk_en(o_clk_en),
            .i_dq(i_dq),.i_wait(i_wait),.cram0_a(a0),.cram1_a(a1),.cram0_dq(dq0),.cram1_dq(dq1),
            .cram0_wait(wait0),.cram1_wait(wait1),.cram0_clk(c0clk),.cram1_clk(c1clk),.cram0_adv_n(adv0),.cram1_adv_n(adv1),
            .cram0_cre(cre0),.cram1_cre(cre1),.cram0_ce0_n(ce00),.cram0_ce1_n(ce01),.cram1_ce0_n(ce10),.cram1_ce1_n(ce11),
            .cram0_oe_n(oe0),.cram1_oe_n(oe1),.cram0_we_n(we0),.cram1_we_n(we1),
            .cram0_ub_n(ub0),.cram0_lb_n(lb0),.cram1_ub_n(ub1),.cram1_lb_n(lb1));
    end endgenerate
    wire chip_clk0,chip_clk1,chip_host_oe;
    wire [21:16] ca0,ca1;
    wire [15:0] cdq0,cdq1;
    wire [17:0] cp;
    // Explicit independent fixture delays: forwarded clock, outgoing address/
    // controls/data, and returning chip data. These are not measured board skew.
    assign #(CLOCK_DELAY) chip_clk0=c0clk;
    assign #(CLOCK_DELAY) chip_clk1=c1clk;
    assign #(CONTROL_DELAY) ca0=a0;
    assign #(CONTROL_DELAY) ca1=a1;
    assign #(CONTROL_DELAY) chip_host_oe=o_dq_oe;
    assign #(CONTROL_DELAY) cp={adv0,adv1,cre0,cre1,ce00,ce01,ce10,ce11,oe0,oe1,we0,we1,ub0,ub1,lb0,lb1,2'b0};
    assign #(CONTROL_DELAY) cdq0=o_dq_oe ? dq0 : 16'bz;
    assign #(CONTROL_DELAY) cdq1=o_dq_oe ? dq1 : 16'bz;
    assign #(RETURN_DELAY) dq0=!o_dq_oe ? cdq0 : 16'bz;
    assign #(RETURN_DELAY) dq1=!o_dq_oe ? cdq1 : 16'bz;
    wire chip_wait0,chip_wait1;
    assign #(RETURN_DELAY) wait0=chip_wait0;
    assign #(RETURN_DELAY) wait1=chip_wait1;
    cellularram_burst_model #(.HIGH_HALF(1),.T_ACLK(DATA_ACCESS)) chip0 (
        .clk(chip_clk0),.host_oe(chip_host_oe),.a(ca0),.dq(cdq0),.ce0_n(cp[13]),.ce1_n(cp[12]),
        .adv_n(cp[17]),.oe_n(cp[9]),.we_n(cp[7]),.ub_n(cp[5]),.lb_n(cp[3]),.cre(cp[15]),.wait_pin(chip_wait0));
    cellularram_burst_model #(.HIGH_HALF(0),.T_ACLK(DATA_ACCESS)) chip1 (
        .clk(chip_clk1),.host_oe(chip_host_oe),.a(ca1),.dq(cdq1),.ce0_n(cp[11]),.ce1_n(cp[10]),
        .adv_n(cp[16]),.oe_n(cp[8]),.we_n(cp[6]),.ub_n(cp[4]),.lb_n(cp[2]),.cre(cp[14]),.wait_pin(chip_wait1));
    function automatic integer slot(input [22:0] w);
        if(w<23'h001000) slot=int'(w);
        else if(w>=23'h3ff000 && w<23'h401000) slot=4096+int'(w-23'h3ff000);
        else if(w>=23'h7ff000) slot=12288+int'(w-23'h7ff000);
        else begin slot=0; $fatal(1,"Host word outside oracle"); end
    endfunction
    function automatic [7:0] initial_byte(input integer a);
        initial_byte=8'(a*17+(a>>8)*23+8'h5a);
    endfunction
    reg [7:0] expected[0:65535];
    initial for(integer i=0;i<16384;i=i+1) begin
        integer w;
        w=i<4096 ? i : i<12288 ? 'h3ff000+i-4096 : 'h7ff000+i-12288;
        for(integer b=0;b<4;b=b+1) expected[i*4+b]=initial_byte(w*4+b);
    end
    reg [22:0] read_queue[0:131071];
    integer head=0,tail=0,compares=0,accepted=0,packets=0;
    always @(posedge clk) if(!rst) begin
        if(valid && ready) begin
            accepted=accepted+1;
            if(write) begin
                for(integer b=0;b<4;b=b+1)
                    if(mask[b]) expected[4*slot({die,address})+b]=data[b*8+:8];
            end else begin read_queue[tail]={die,address};tail=tail+1;end
        end
        if(rvalid) begin
            if(head>=tail) $fatal(1,"Unexpected burst return");
            for(integer b=0;b<4;b=b+1) begin
                if(read_data[b*8+:8]!==expected[slot(read_queue[head])*4+b])
                    $fatal(1,"Burst read byte mismatch addr=%h got=%h lane=%0d",read_queue[head],read_data,b);
                compares=compares+1;
            end
            head=head+1;
        end
        if(wait_error) $fatal(1,"Burst WAIT at expected valid data");
    end
    task automatic send(input [22:0] a,input bit wr,input [31:0] d,input [3:0] bm,input bit end_run);
        integer waited;
        begin
            @(negedge clk);valid=1;{die,address}=a;write=wr;data=d;mask=bm;last=end_run;
            waited=0;
            @(posedge clk);while(!ready) begin
                @(posedge clk);waited=waited+1;if(waited>100) $fatal(1,"Burst command timeout");
            end
        end
    endtask
    task automatic drain;
        begin
            @(negedge clk); valid=0;
            while(!idle || head!=tail) @(negedge clk);
        end
    endtask
    task automatic packet(input [22:0] a,input integer words,input bit wr,input [3:0] bm,input [31:0] d);
        begin
            for(integer i=0;i<words;i=i+1) send(a+23'(i),wr,d^32'(i*32'h10204081),bm,i==words-1);
            drain();packets=packets+1;
        end
    endtask
    task automatic wait_init;
        integer waited;
        begin
            waited=0;while(!initialized) begin @(negedge clk);waited=waited+1;
                if(waited>20000) $fatal(1,"PSRAM initialization timeout");end
            if(chip0.bcr[0]!=16'h641f || chip0.bcr[1]!=16'h641f ||
               chip1.bcr[0]!=16'h641f || chip1.bcr[1]!=16'h641f ||
               chip0.rcr[0]!=16'h0010 || chip0.rcr[1]!=16'h0010 ||
               chip1.rcr[0]!=16'h0010 || chip1.rcr[1]!=16'h0010)
                $fatal(1,"Paired die config mismatch");
        end
    endtask
    task automatic reset_active(input bit wr,input integer pause);
        begin
            send(23'h30,wr,32'hf00d1234,4'hf,0);
            @(negedge clk);valid=0;repeat(pause) @(negedge clk);rst=1;
            repeat(20) @(negedge clk);head=tail;rst=0;wait_init();
            packet(23'h30,4,0,0,0);
        end
    endtask
    task automatic supply(input integer words,input bit wr);
        realtime start,elapsed;
        begin
            start=$realtime;
            for(integer p=0;p<128;p=p+1) packet(23'((p%8)*128),words,wr,4'hf,32'(p));
            elapsed=$realtime-start;
            $display("PSRAM_SUPPLY words=%0d write=%0d bytes=%0d elapsed_ns=%0.3f MBps=%0.3f",words,wr,
                128*words*4,elapsed,real'(128*words*4)*1000.0/elapsed);
        end
    endtask
    reg [31:0] rng=32'h50535242;
    function automatic [31:0] random_next(input [31:0] v);
        reg [31:0] x;
        begin x=v^(v<<13);x=x^(x>>17);random_next=x^(x<<5);end
    endfunction
    initial begin
        repeat(4) @(negedge clk);rst=0;wait_init();
        if(MEASURE_ONLY) begin
            supply(1,0);supply(4,0);supply(16,0);supply(128,0);
            supply(1,1);supply(4,1);supply(16,1);supply(128,1);
            $display("PSRAM_SUPPLY PASS compares=%0d",compares);$finish;
        end
        for(integer region=0;region<4;region=region+1) for(integer bm=0;bm<16;bm=bm+1) begin
            reg [22:0] a;
            a=region==0 ? 0 : region==1 ? 23'h400000 : region==2 ? 23'h3ff000 : 23'h7ff000;
            a=a+23'(120+bm*256);rng=random_next(rng);
            packet(a,16,1,4'(bm),rng);packet(a,16,0,0,0);
        end
        for(integer n=0;n<300;n=n+1) begin
            reg [22:0] a;
            integer words,offset;
            rng=random_next(rng); words=1+int'(rng[7:0]);
            offset=int'(rng[23:8])%(4096-words);
            a=n%4==0 ? 0 : n%4==1 ? 23'h400000 : n%4==2 ? 23'h3ff000 : 23'h7ff000;
            a=a+23'(offset);
            packet(a,words,1,4'(rng[31:28]),rng);packet(a,words,0,0,0);
        end
        packet(23'h3fffff,2,1,4'hf,32'h12345678);packet(23'h3fffff,2,0,0,0);
        packet(23'h7fff80,128,1,4'hf,32'hfeedbeef);packet(23'h7fff80,128,0,0,0);
        // No LAST: a different die/address/direction must restart the bus.
        send(0,1,32'hdeaddead,4'hf,0);send(23'h400008,1,32'hbeefbeef,4'hf,0);
        send(23'h400020,1,32'h01020304,4'h3,0);send(23'h400020,0,0,0,1);drain();
        packet(0,1,0,0,0);packet(23'h400008,1,0,0,0);
        reset_active(0,0);reset_active(1,0);
        // Reset before the first port acceptance. A masked/dummy transfer
        // drains the pins; no CPU word is accepted or returned after reset.
        for(integer abort_write=0;abort_write<2;abort_write=abort_write+1) begin
            @(negedge clk);valid=1;die=1;address=22'h10;write=1'(abort_write);last=1;
            data=32'habcdef12;mask=4'hf;
            repeat(2) @(negedge clk);rst=1;valid=0;
            repeat(20) @(negedge clk);head=tail;rst=0;wait_init();packet(23'h400010,1,0,0,0);
        end
        if(head!=tail) $fatal(1,"Missing read returns");
        $display("PSRAM_BURST PASS packets=%0d accepted_words=%0d compared_bytes=%0d reads=%0d writes=%0d configs=%0d delay_ns=%g access_ns=%g",
            packets,accepted,compares,chip0.reads,chip0.writes,chip0.config_writes,CLOCK_DELAY,DATA_ACCESS);
        $finish;
    end
    integer trace_file;
    reg [8191:0] trace_path;
    initial if(TRACE) begin
        if(!$value$plusargs("trace=%s",trace_path)) $fatal(1,"Trace path required");
        trace_file=$fopen(trace_path,"w");
    end
    always @(negedge clk) if(TRACE)
        $fwrite(trace_file,"%0t %b %b %b %b %b %08x\n",$time,initialized,ready,idle,wait_error,rvalid,rvalid?read_data:32'b0);
    initial begin #20000000;$fatal(1,"Burst PSRAM watchdog");end
endmodule
