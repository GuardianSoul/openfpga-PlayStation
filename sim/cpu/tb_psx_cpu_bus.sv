`timescale 1ns/1ps
`default_nettype none
module tb_psx_cpu_bus;
    reg clk=0,rst=1,cache_isolated=0;
    always #5 clk=!clk;
    reg bus_valid=0,bus_write=0,bus_fetch=0,bus_rsp_ready=0;
    reg [31:0] bus_addr=0,bus_wdata=0,bus_store_raw=0;
    reg [3:0] bus_byte_en=0;
    reg [1:0] bus_size=2;
    wire bus_ready,bus_rsp_valid,bus_rsp_error;
    wire [31:0] bus_rsp_rdata;
    wire mem_valid,mem_write,mem_rsp_ready,io_valid,io_write,io_rsp_ready,unsupported_cache_mode;
    wire [24:0] mem_addr;
    wire [3:0] mem_halfwords,io_byte_en;
    wire [127:0] mem_wdata;
    wire [15:0] mem_byte_en;
    wire [31:0] io_addr,io_wdata,io_store_raw;
    wire [1:0] io_size;
    reg mem_ready=0,mem_rsp_valid=0,mem_rsp_error=0,io_ready=0,io_rsp_valid=0,io_rsp_error=0;
    reg [127:0] mem_rsp_rdata=0;
    reg [31:0] io_rsp_rdata=0;
    reg mem_pending=0,io_pending=0;
    integer mem_delay=0,io_delay=0,mem_count=0,io_count=0,checked=0;
    reg [31:0] rng=32'h50535809;
    integer wanted_target=0;
    reg [24:0] wanted_address;
    reg [31:0] wanted_io_address;
    reg wanted_write;
    reg [3:0] wanted_mask;
    reg [31:0] wanted_data,wanted_raw;
    reg [1:0] wanted_size;
    psx_cpu_bus dut (.*);
    always @(posedge clk) begin
        if (rst) begin
            mem_ready<=0;mem_pending<=0;mem_rsp_valid<=0;
            io_ready<=0;io_pending<=0;io_rsp_valid<=0;
        end else begin
            rng<=rng*32'd1664525+32'd1013904223;
            mem_ready<=!mem_pending && !mem_rsp_valid && rng[0];
            io_ready<=!io_pending && !io_rsp_valid && rng[3];
            if(mem_valid && mem_ready) begin
                if(wanted_target!=1 || mem_addr!==wanted_address || mem_halfwords!=2 ||
                   mem_write!==wanted_write || mem_byte_en!=={12'b0,wanted_mask} || mem_wdata!=={96'b0,wanted_data})
                    $fatal(1,"Guest memory route/payload mismatch");
                mem_count=mem_count+1;mem_ready<=0;mem_pending<=1;mem_delay<=3+integer'(rng[7:4]);
                mem_rsp_rdata<={96'b0,32'hA5000000 ^ {7'b0,mem_addr}};
                mem_rsp_error<=mem_addr==25'h1236;
            end
            if(io_valid && io_ready) begin
                if(wanted_target!=2 || io_addr!==wanted_io_address || io_write!==wanted_write ||
                   io_wdata!==wanted_data || io_store_raw!==wanted_raw || io_byte_en!==wanted_mask || io_size!==wanted_size)
                    $fatal(1,"Guest peripheral route/payload mismatch");
                io_count=io_count+1;io_ready<=0;io_pending<=1;io_delay<=2+integer'(rng[11:8]);
                io_rsp_rdata<=32'h5A000000 ^ io_addr;io_rsp_error<=io_addr==32'h1F8020FC;
            end
            if(mem_pending) begin
                if(mem_delay==0) begin mem_pending<=0;mem_rsp_valid<=1;end else mem_delay<=mem_delay-1;
            end
            if(io_pending) begin
                if(io_delay==0) begin io_pending<=0;io_rsp_valid<=1;end else io_delay<=io_delay-1;
            end
            if(mem_rsp_valid && mem_rsp_ready)mem_rsp_valid<=0;
            if(io_rsp_valid && io_rsp_ready)io_rsp_valid<=0;
        end
    end
    task automatic check(
        input reg [31:0] address,input reg write_flag,input reg fetch_flag,
        input reg [3:0] mask,input reg [31:0] data,input reg [31:0] raw,input reg [1:0] size,
        input integer target,input reg [24:0] mapped,input reg error,input reg [31:0] expected
    );
        integer old_memory,old_io;
        begin
            @(negedge clk); while(!bus_ready)@(negedge clk);
            old_memory=mem_count;old_io=io_count;
            wanted_target=target;wanted_address=mapped;wanted_io_address=address & 32'h1FFFFFFF;
            wanted_write=write_flag;wanted_mask=mask;wanted_data=data;wanted_raw=raw;wanted_size=size;
            bus_valid=1;bus_addr=address;bus_write=write_flag;bus_fetch=fetch_flag;
            bus_byte_en=mask;bus_wdata=data;bus_store_raw=raw;bus_size=size;bus_rsp_ready=0;
            @(negedge clk);bus_valid=0;bus_addr=~address;bus_write=!write_flag;bus_fetch=!fetch_flag;
            bus_wdata=~data;bus_store_raw=~raw;bus_byte_en=0;bus_size=3;
            while(!bus_rsp_valid)@(negedge clk);
            repeat(5)begin
                if(bus_rsp_error!==error || bus_rsp_rdata!==expected || bus_ready)
                    $fatal(1,"Guest response mismatch/stability address=%h got=%h expected=%h",address,bus_rsp_rdata,expected);
                @(negedge clk);
            end
            if(mem_count-old_memory!=(target==1) || io_count-old_io!=(target==2))$fatal(1,"Guest request duplication");
            bus_rsp_ready=1;@(negedge clk);bus_rsp_ready=0;checked=checked+1;
        end
    endtask
    initial begin
        repeat(4)@(negedge clk);rst=0;
        for(integer mirror=0;mirror<4;mirror=mirror+1)begin
            for(integer segment=0;segment<3;segment=segment+1)begin
                check(32'h1000+32'(mirror)*32'h200000+(segment==0?32'b0:segment==1?32'h80000000:32'hA0000000),
                      0,0,15,0,0,2,1,25'h800,0,32'hA5000800);
            end
        end
        check(32'hBFC00000,0,1,15,0,0,2,1,25'h800000,0,32'hA5800000);
        check(32'h1FC7FFFC,0,0,15,0,0,2,1,25'h83FFFE,0,32'hA583FFFE);
        check(32'h9FC00004,0,0,15,0,0,2,1,25'h800002,0,32'hA5800002);
        check(32'hBFC00000,1,0,15,32'hDEADBEEF,32'hDEADBEEF,2,0,0,0,0);
        for(integer lane=0;lane<4;lane=lane+1)
            check(32'h1000+32'(lane),1,0,4'b0001<<lane,32'hA5<<(lane*8),32'h123400A5,0,1,25'h800,0,0);
        check(32'h1F800000,1,0,15,32'hAABBCCDD,32'hAABBCCDD,2,0,0,0,0);
        check(32'h9F800000,0,0,15,0,0,2,0,0,0,32'hAABBCCDD);
        check(32'h1F800001,1,0,2,32'h00001100,32'h11,0,0,0,0,0);
        check(32'h1F800000,0,0,15,0,0,2,0,0,0,32'hAABB11DD);
        check(32'hBF800000,0,0,15,0,0,2,0,0,1,0);
        check(32'h1F800000,0,1,15,0,0,2,0,0,1,0);
        check(32'h1F800400,0,0,15,0,0,2,0,0,1,0);
        check(32'hBF801080,1,0,3,32'hDEADBEEF,32'hDEADBEEF,1,2,0,0,0);
        check(32'h9F801070,0,0,15,0,0,2,2,0,0,32'h45801070);
        check(32'h1F8020FC,0,0,15,0,0,2,2,0,1,32'h458020FC);
        check(32'h1F801080,0,1,15,0,0,2,0,0,1,0);
        check(32'h246C,0,0,15,0,0,2,1,25'h1236,1,32'hA5001236);
        check(32'hFFFE0130,1,0,15,32'h804,32'h804,2,0,0,0,0);
        check(32'hFFFE0130,0,0,15,0,0,2,0,0,0,32'h804);
        check(32'h20000000,0,0,15,0,0,2,0,0,1,0);
        check(32'hC0000000,0,0,15,0,0,2,0,0,1,0);
        check(32'h800000,0,0,15,0,0,2,0,0,1,0);
        cache_isolated=1;
        check(32'h80001000,1,0,15,32'hDEADBEEF,32'hDEADBEEF,2,0,0,1,0);
        if(!unsupported_cache_mode)$fatal(1,"Missing unsupported isolated-cache diagnostic");
        check(32'hA0001000,0,0,15,0,0,2,1,25'h800,0,32'hA5000800);
        $display("PASS guest bus checked=%0d memory_requests=%0d peripheral_requests=%0d",checked,mem_count,io_count);
        $finish;
    end
    initial begin #1000000;$fatal(1,"Guest bus test timeout");end
endmodule
`default_nettype wire
