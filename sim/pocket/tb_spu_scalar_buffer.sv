// SPDX-License-Identifier: MIT
`timescale 1ns/1ps
module tb_spu_scalar_buffer;
    parameter TEST_CANCEL=0;
    parameter real CORE_HALF=14.76255,MEM_HALF=8.0,MEM_PHASE=0.0;
    reg clk=0,mem_clk=0,reset=1,ce=1,discard=0,request=0,rnw=1;
    always #(CORE_HALF) clk=~clk;
    initial begin #(MEM_PHASE);forever #(MEM_HALF) mem_clk=~mem_clk;end
    reg [18:0] address=0;reg [31:0] data=0;reg [3:0] be=3;
    wire done,idle,fault,pv,pw,pr,rv,rr,re;wire [31:0] read_data;
    wire [24:0] pa;wire [3:0] pn;wire [127:0] pd,rd;wire [15:0] pb;
    wire mv,mw,mready,mrv;wire [23:0] ma;wire [31:0] md,mread;wire [3:0] mb;
    wire src_reset,dst_reset;
    psx_spu_sdram_adapter dut(.clk1x(clk),.mem_clk(mem_clk),.reset_async(reset),.core_ce(ce),.discard_returns(discard),
        .request(request),.rnw(rnw),.addr(address),.data(data),.byte_en(be),.done(done),.read_data(read_data),
        .source_idle(idle),.fault(fault),.p_valid(pv),.p_write(pw),.p_ready(pr),.p_addr(pa),.p_halfwords(pn),
        .p_wdata(pd),.p_byte_en(pb),.p_rsp_valid(rv),.p_rsp_ready(rr),.p_rsp_error(re),.p_rsp_rdata(rd));
    assign src_reset=dut.src_reset,dst_reset=dut.dst_reset;
    psx_mem_bridge #(.PIPELINE_READS(1)) bridge(.clk(mem_clk),.rst(dst_reset),
        .req_valid(pv),.req_ready(pr),.req_write(pw),.req_addr(pa),.req_halfwords(pn),.req_wdata(pd),.req_byte_en(pb),
        .rsp_valid(rv),.rsp_ready(rr),.rsp_rdata(rd),.rsp_error(re),
        .mem_valid(mv),.mem_ready(mready),.mem_write(mw),.mem_addr(ma),.mem_wdata(md),.mem_byte_en(mb),
        .mem_rvalid(mrv),.mem_rdata(mread));
    reg [31:0] backing[0:131071];reg [7:0] gold[0:524287];
    reg [2:0] response=0;reg [2:0][31:0] response_data=0;
    integer mem_cycles=0,physical_reads=0,physical_writes=0,requests=0,read_checks=0,paused=0;
    assign mready=!dst_reset && mem_cycles%7!=0;
    assign mrv=response[2],mread=response_data[2];
    always @(negedge mem_clk) mem_cycles++;
    always @(posedge mem_clk) begin
        response<={response[1:0],1'b0};response_data<={response_data[1:0],32'b0};
        if(dst_reset) response<=0;
        else if(mv && mready) begin
            if(ma<24'h400000 || ma>=24'h420000) $fatal(1,"Scalar buffer backend address escaped");
            if(mw) begin
                for(integer b=0;b<4;b++) if(mb[b]) backing[ma-24'h400000][8*b+:8]<=md[8*b+:8];
                physical_writes++;
            end else begin response[0]<=1;response_data[0]<=backing[ma-24'h400000];physical_reads++;end
        end
    end
    always @(posedge clk) if(!src_reset && fault) $fatal(1,"Scalar buffer protocol fault");
    task access(input bit write,input [18:0] a,input [15:0] d,input [1:0] mask,input bit pause_return);
        reg [15:0] want;
        wait(idle);@(negedge clk);address=a;data={16'b0,d};be={2'b0,mask};rnw=!write;request=1;
        if(write) for(integer b=0;b<2;b++) if(mask[b]) gold[int'(a)+b]=d[8*b+:8];
        want=write ? 0:{gold[int'(a)+1],gold[int'(a)]};
        @(negedge clk);request=0;address='1;data='1;be=0;
        if(pause_return) begin
            ce=0;repeat(60) begin @(negedge clk);if(done || idle) $fatal(1,"Scalar buffer pending CE return escaped");end
            ce=1;paused++;
        end
        wait(done);
        if(read_data!=={16'b0,want}) $fatal(1,"Scalar buffer read mismatch a=%h got=%h want=%h",a,read_data,want);
        if(!write) read_checks++;
        @(negedge clk);requests++;
    endtask
    reg [31:0] random_state=32'h17eab946;reg [18:0] a=0;
    initial begin
        for(integer i=0;i<524288;i++) gold[i]=8'((i*73)^(i>>8)^'hbd);
        for(integer i=0;i<131072;i++) backing[i]={gold[i*4+3],gold[i*4+2],gold[i*4+1],gold[i*4]};
        repeat(8) @(negedge clk);reset=0;wait(idle);
        for(integer op=0;op<1200;op++) begin
            random_state=random_state^(random_state<<13);random_state=random_state^(random_state>>17);random_state=random_state^(random_state<<5);
            if(op%4==0) a={random_state[18:1],1'b0};
            access(op%4==1,a,random_state[15:0],2'(op%3+1),op%23==0);
            if(op%4==2) a=a^19'h00000e;
        end
        // Repeated hits, then explicit reload invalidation while no source job is live.
        access(0,19'h6000,0,3,0);access(0,19'h6000,0,3,1);
        wait(idle);@(negedge clk);discard=1;
        backing['h6000/4]=32'hdef01234;gold['h6000]='h34;gold['h6001]='h12;gold['h6002]='hf0;gold['h6003]='hde;
        repeat(4) @(negedge clk);discard=0;
        access(0,19'h6000,0,3,0);access(0,19'h6002,0,3,0);
        if(TEST_CANCEL) begin : cancelled_fill
            integer drain_age;
            wait(idle);@(negedge clk);discard=1;repeat(4) @(negedge clk);discard=0;
            address=19'h5c000;rnw=1;request=1;be=3;
            @(negedge clk);request=0;address='1;be=0;
            if(TEST_CANCEL==3) wait(bridge.fill_valid);else if(TEST_CANCEL==2) wait(rv);else wait(dut.state==5);
            @(negedge clk);ce=0;discard=1;
            @(negedge clk);discard=0;
            drain_age=0;
            while(!idle) begin
                @(negedge clk);drain_age++;
                if(done || drain_age>100) $fatal(1,"Scalar buffer cancelled fill did not drain outside CE");
            end
            // Simulate a host reload only after the abandoned operation drains.
            backing['h5c000/4]=32'habcd0123;
            gold['h5c000]='h23;gold['h5c001]='h01;gold['h5c002]='hcd;gold['h5c003]='hab;
            ce=1;repeat(4) begin @(negedge clk);if(done) $fatal(1,"Scalar buffer discarded completion escaped");end
            access(0,19'h5c000,0,3,0);access(0,19'h5c002,0,3,0);
            // Reset while a read fill is live; its completion must not escape.
            wait(idle);@(negedge clk);address=19'h5d000;rnw=1;request=1;be=3;
            @(negedge clk);request=0;address='1;be=0;
            if(TEST_CANCEL==3) wait(bridge.fill_valid);else if(TEST_CANCEL==2) wait(rv);else wait(dut.state==5);
            @(negedge clk);reset=1;repeat(8) @(negedge clk);reset=0;wait(idle);
            access(0,19'h5d000,0,3,0);
        end
        // Full reset invalidates tags too, retaining external backing bytes.
        @(negedge clk);reset=1;repeat(8) @(negedge clk);reset=0;wait(idle);
        access(0,19'h7fffe,0,3,1);access(1,19'h7fffe,16'ha571,1,0);access(0,19'h7fffe,0,3,0);
        $display("PASS scalar buffer checks requests=%0d reads=%0d CE_pauses=%0d physical=%0d/%0d",requests,read_checks,paused,physical_reads,physical_writes);
        $finish;
    end
    initial begin #10000000;$fatal(1,"Scalar buffer checks timeout");end
endmodule
