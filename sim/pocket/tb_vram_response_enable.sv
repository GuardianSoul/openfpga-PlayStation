// SPDX-License-Identifier: MIT
`timescale 1fs/1fs
module vram_response_fixture #(
    parameter CANDIDATE=0
) (
    input wire src_clk,dst_clk,reset,
    input wire rd,we,
    input wire [27:0] addr,
    input wire [7:0] count,be,
    input wire [63:0] din,
    input wire block_request,block_response,
    output wire busy,ready,fault,
    output wire [63:0] dout,
    output wire req_valid,req_ready,req_write,rsp_valid,rsp_ready,rsp_error,
    output wire [24:0] req_addr,
    output wire [3:0] req_count,
    output wire [127:0] req_data,rsp_data,
    output wire [15:0] req_be,
    output wire d_valid,d_ready,d_write,d_rsp_valid,d_rsp_ready,d_rsp_error,
    output wire [24:0] d_addr,
    output wire [3:0] d_halfwords,
    output wire [127:0] d_wdata,d_rsp_rdata,
    output wire [15:0] d_byte_en,
    output wire src_reset,dst_reset,
    output wire advance
);
    generate if(CANDIDATE) begin: candidate
        psx_vram_response_adapter adapter(.clk(src_clk),.reset(reset),.*,
            .rsp_advance(advance));
        psx_mem_cdc_response_enable cdc(.reset_async(reset),.*,
            .s_valid(req_valid),.s_ready(req_ready),.s_write(req_write),.s_addr(req_addr),
            .s_halfwords(req_count),.s_wdata(req_data),.s_byte_en(req_be),
            .s_rsp_valid(rsp_valid),.s_rsp_ready(rsp_ready),.s_rsp_rdata(rsp_data),.s_rsp_error(rsp_error),
            .s_rsp_advance(advance));
    end else begin: original
        psx_vram_adapter adapter(.clk(src_clk),.reset(reset),.*);
        psx_mem_cdc cdc(.reset_async(reset),.*,
            .s_valid(req_valid),.s_ready(req_ready),.s_write(req_write),.s_addr(req_addr),
            .s_halfwords(req_count),.s_wdata(req_data),.s_byte_en(req_be),
            .s_rsp_valid(rsp_valid),.s_rsp_ready(rsp_ready),.s_rsp_rdata(rsp_data),.s_rsp_error(rsp_error));
        assign advance=0;
    end endgenerate
    reg pending=0,valid=0,error=0;
    reg [127:0] response=0;
    integer delay_left=0;
    assign d_ready=!dst_reset && !block_request && !pending && !valid;
    assign d_rsp_valid=valid;
    assign d_rsp_error=error;
    assign d_rsp_rdata=response;
    function automatic [127:0] reply(input [24:0] address,input [127:0] data,input [15:0] mask);
        reply=data^{4{7'h53,address}}^{112'h0,mask};
    endfunction
    always @(posedge dst_clk or posedge reset) begin
        if(reset) begin pending<=0;valid<=0;error<=0;response<=0;delay_left<=0;end
        else if(dst_reset) begin pending<=0;valid<=0;error<=0;response<=0;end
        else begin
            if(d_valid && d_ready) begin
                if(d_halfwords!=4) $fatal(1,"VRAM oracle packet length");
                pending<=1;
                delay_left<=3+integer'(d_addr[4:0]);
                response<=reply(d_addr,d_wdata,d_byte_en);
                error<=d_addr[4:0]==31;
            end
            if(pending && !block_response) begin
                if(delay_left==0) begin pending<=0;valid<=1;end
                else delay_left<=delay_left-1;
            end
            if(valid && d_rsp_ready) valid<=0;
        end
    end
endmodule

module tb_vram_response_enable;
    parameter integer SRC_HALF=7381275,DST_HALF=8000000,DST_PHASE=3000000;
    reg src_clk=0,dst_clk=0,src_run=1,dst_run=1,reset=0;
    always #(SRC_HALF) if(src_run) src_clk=~src_clk;
    initial begin #(DST_PHASE);forever #(DST_HALF) if(dst_run) dst_clk=~dst_clk;end
    reg rd=0,we=0,block_request=0,block_response=0;
    reg [27:0] addr=0;
    reg [7:0] count=0,be=0;
    reg [63:0] din=0;
    wire [1:0] busy,ready,fault,req_valid,req_ready,req_write,rsp_valid,rsp_ready,rsp_error;
    wire [1:0] d_valid,d_ready,d_write,d_rsp_valid,d_rsp_ready,d_rsp_error,src_reset,dst_reset,advance;
    wire [63:0] dout[2];
    wire [24:0] req_addr[2],d_addr[2];
    wire [3:0] req_count[2],d_halfwords[2];
    wire [127:0] req_data[2],rsp_data[2],d_wdata[2],d_rsp_rdata[2];
    wire [15:0] req_be[2],d_byte_en[2];
    genvar k;
    generate for(k=0;k<2;k=k+1) begin: path
        vram_response_fixture #(.CANDIDATE(k)) fixture(.*,
            .busy(busy[k]),.ready(ready[k]),.fault(fault[k]),.dout(dout[k]),
            .req_valid(req_valid[k]),.req_ready(req_ready[k]),.req_write(req_write[k]),
            .rsp_valid(rsp_valid[k]),.rsp_ready(rsp_ready[k]),.rsp_error(rsp_error[k]),
            .req_addr(req_addr[k]),.req_count(req_count[k]),.req_data(req_data[k]),
            .rsp_data(rsp_data[k]),.req_be(req_be[k]),
            .d_valid(d_valid[k]),.d_ready(d_ready[k]),.d_write(d_write[k]),
            .d_rsp_valid(d_rsp_valid[k]),.d_rsp_ready(d_rsp_ready[k]),.d_rsp_error(d_rsp_error[k]),
            .d_addr(d_addr[k]),.d_halfwords(d_halfwords[k]),.d_wdata(d_wdata[k]),
            .d_rsp_rdata(d_rsp_rdata[k]),.d_byte_en(d_byte_en[k]),
            .src_reset(src_reset[k]),.dst_reset(dst_reset[k]),.advance(advance[k]));
    end endgenerate
    integer source_checks=0,dest_checks=0,responses=0,oracle_reads=0,resets=0;
    integer completed_commands=0,completed_beats=0;
    reg [127:0] expected=0;
    reg [31:0] rng=32'h76543210;
    reg [63:0] old_dout=0;
    reg advanced=0;
    always @(posedge src_clk) begin
        if(!reset && ready[0]) begin
            if(dout[0]!==expected[63:0]) $fatal(1,"VRAM independent return oracle");
            oracle_reads++;
        end
    end
    always @(negedge src_clk) begin
        source_checks++;
        if({busy[0],ready[0],fault[0],dout[0],req_valid[0],req_ready[0],req_write[0],
            req_addr[0],req_count[0],req_data[0],req_be[0],rsp_valid[0],rsp_ready[0],
            rsp_error[0],rsp_data[0],src_reset[0]} !==
           {busy[1],ready[1],fault[1],dout[1],req_valid[1],req_ready[1],req_write[1],
            req_addr[1],req_count[1],req_data[1],req_be[1],rsp_valid[1],rsp_ready[1],
            rsp_error[1],rsp_data[1],src_reset[1]}) $fatal(1,"VRAM source cycle lockstep");
        if(!reset && !ready[0] && dout[0]!==old_dout)
            $fatal(1,"VRAM output changed outside ready edge");
        old_dout=dout[0];
        if(!reset && !src_reset[1]) begin
            if(advanced && !rsp_valid[1]) $fatal(1,"VRAM advance was not followed by valid");
            advanced=advance[1];
        end else advanced=0;
    end
    always @(posedge dst_clk) if(!reset && d_valid[0] && d_ready[0]) begin
        expected=path[0].fixture.reply(d_addr[0],d_wdata[0],d_byte_en[0]);
        completed_beats++;
    end
    always @(negedge dst_clk) begin
        dest_checks++;
        if({d_valid[0],d_ready[0],d_write[0],d_addr[0],d_halfwords[0],d_wdata[0],
            d_byte_en[0],d_rsp_valid[0],d_rsp_ready[0],d_rsp_rdata[0],d_rsp_error[0],dst_reset[0]} !==
           {d_valid[1],d_ready[1],d_write[1],d_addr[1],d_halfwords[1],d_wdata[1],
            d_byte_en[1],d_rsp_valid[1],d_rsp_ready[1],d_rsp_rdata[1],d_rsp_error[1],dst_reset[1]})
            $fatal(1,"VRAM destination cycle lockstep");
        if(d_rsp_valid[0] && d_rsp_ready[0]) responses++;
    end
    task reset_both;
        #1000000;reset=1;rd=0;we=0;resets++;
        #200000000;
        src_run=1;dst_run=1;
        // The unchanged adapter has synchronous reset: a stopped source
        // clock must resume while common reset is still asserted.
        repeat(4) @(negedge src_clk);reset=0;
        repeat(16) @(negedge src_clk);
        if(busy!=0 || ready!=0 || fault!=0 || d_valid!=0 || rsp_valid!=0)
            $fatal(1,"VRAM stale handshake after reset busy=%b ready=%b fault=%b dvalid=%b rspvalid=%b",
                busy,ready,fault,d_valid,rsp_valid);
    endtask
    task launch(input integer id);
        @(negedge src_clk);
        while(busy[0]) @(negedge src_clk);
        rng=rng*32'd1664525+32'd1013904223;
        addr={rng[27:3],3'b0};count=8'(1+id%255);be=rng[7:0];
        din={rng,rng^32'h12345678};rd=!id[0];we=id[0];
        @(negedge src_clk);rd=0;we=0;addr=~addr;be=~be;din=~din;count=0;
    endtask
    task finish_command;
        integer waited;
        waited=0;
        while(busy[0]) begin
            @(negedge src_clk);waited++;
            if(waited>100000) $fatal(1,"VRAM command timeout");
        end
        completed_commands++;
    endtask
    initial begin
        reset_both();
        for(integer id=0;id<260;id++) begin launch(id);finish_command();end
        block_request=1;launch(261);wait(d_valid[0]);reset_both();block_request=0;
        launch(262);finish_command();
        block_response=1;launch(263);wait(path[0].fixture.pending);
        dst_run=0;reset_both();block_response=0;
        launch(264);finish_command();
        block_response=1;launch(265);wait(path[0].fixture.pending);
        src_run=0;reset_both();block_response=0;
        launch(266);finish_command();
        launch(267);wait(rsp_valid[0]);reset_both();
        for(integer id=268;id<300;id++) begin launch(id);finish_command();end
        // Invalid commands still produce the exact original sticky fault.
        @(negedge src_clk);rd=1;we=1;count=0;addr=3;
        @(negedge src_clk);rd=0;we=0;finish_command();
        if(!fault[0] || source_checks<100000 || oracle_reads<15000 || resets!=5)
            $fatal(1,"VRAM response coverage insufficient checks=%0d reads=%0d resets=%0d",
                source_checks,oracle_reads,resets);
        $display("PASS VRAM response enable commands=%0d source_checks=%0d dest_checks=%0d beats=%0d reads=%0d resets=%0d",
            completed_commands,source_checks,dest_checks,completed_beats,oracle_reads,resets);
        $finish;
    end
    initial begin #200000000000000; $fatal(1,"VRAM lockstep watchdog");end
endmodule
