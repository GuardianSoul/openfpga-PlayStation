// SPDX-License-Identifier: MIT
`timescale 1ns/1ps
module tb_spu_scalar_stream_bridge;
    reg clk=0,rst=1,qv=0,qw=0,rr=0;
    always #8 clk=~clk;
    reg [24:0] qa=0;reg [3:0] qn=0;
    reg [127:0] qd=0;reg [15:0] qb=0;
    wire qr,rv,re,mv,mw,mready,mrv,fv;
    wire [127:0] rd;wire [23:0] ma;wire [31:0] md,mdata,fd;wire [3:0] mb;wire [1:0] fi;
    psx_spu_scalar_bridge dut(.clk(clk),.rst(rst),.req_valid(qv),.req_ready(qr),.req_write(qw),
        .req_addr(qa),.req_halfwords(qn),.req_wdata(qd),.req_byte_en(qb),
        .rsp_valid(rv),.rsp_ready(rr),.rsp_rdata(rd),.rsp_error(re),
        .mem_valid(mv),.mem_ready(mready),.mem_write(mw),.mem_addr(ma),.mem_wdata(md),.mem_byte_en(mb),
        .mem_rvalid(mrv),.mem_rdata(mdata),.fill_valid(fv),.fill_word(fi),.fill_data(fd));
    function automatic [31:0] pattern(input [23:0] a);pattern=32'h8a24f011^(a*32'h9e3779b1);endfunction
    reg [1:0] pending=0;reg [1:0][31:0] values=0;
    integer cycles=0,words=0,fills=0,commands=0;
    reg [23:0] expected_base=0;reg [31:0] expected_write=0;reg [3:0] expected_mask=0;
    assign mready=cycles%3!=0,mrv=pending[1],mdata=values[1];
    always @(negedge clk) cycles++;
    always @(posedge clk) begin
        pending<={pending[0],1'b0};values<={values[0],32'b0};
        if(rst) pending<=0;
        else begin
            if(mv && mready) begin
                if(ma!==expected_base+(mw ? 0:words)) $fatal(1,"Scalar endpoint native address got=%h expected_base=%h word=%0d state=%0d issued=%0d",ma,expected_base,words,dut.state,dut.issued);
                if(mw) begin
                    if(md!==expected_write || mb!==expected_mask) $fatal(1,"Scalar endpoint masked write");
                end else begin pending[0]<=1;values[0]<=pattern(ma);end
                words++;
            end
            if(fv) begin
                if(fi!==2'(fills) || fd!==pattern(expected_base+fills)) $fatal(1,"Scalar endpoint fill order/data");
                fills++;
            end
        end
    end
    task command(input bit write,input [24:0] a,input [3:0] n,input [1:0] mask,input bit invalid);
        wait(qr);@(negedge clk);words=0;fills=0;expected_base=a[24:1];
        expected_write=a[0] ? 32'hc5a70000:32'h0000c5a7;
        expected_mask=a[0] ? {mask,2'b0}:{2'b0,mask};
        qv=1;qw=write;qa=a;qn=n;qd=128'hc5a7;qb={14'b0,mask};
        @(negedge clk);qv=0;qa='1;qn=0;qd='1;qb=0;
        wait(rv);
        repeat(11) begin
            @(negedge clk);
            if(!rv || re!==invalid || rd!==0 || mv || fv) $fatal(1,"Scalar endpoint held/error reply");
        end
        if(words!=(invalid ? 0:write ? 1:4) || fills!=(invalid || write ? 0:4))
            $fatal(1,"Scalar endpoint command coverage");
        rr=1;@(negedge clk);rr=0;commands++;
    endtask
    initial begin
        repeat(6) @(negedge clk);rst=0;
        command(0,25'h800000,0,3,1);command(0,25'h800000,2,3,1);
        command(0,25'h800000,9,3,1);command(0,25'h800001,8,3,1);
        command(1,25'h800000,2,3,1);command(1,25'h800000,8,3,1);
        command(0,25'h800000,8,3,0);command(0,25'h1fffff8,8,3,0);
        for(integer lane=0;lane<2;lane++) for(integer mask=0;mask<4;mask++)
            command(1,25'h800000+lane,1,2'(mask),0);
        $display("PASS scalar stream endpoint commands=%0d",commands);$finish;
    end
    initial begin #1000000;$fatal(1,"Scalar endpoint timeout");end
endmodule
