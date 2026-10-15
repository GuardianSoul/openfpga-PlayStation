// SPDX-License-Identifier: MIT
`timescale 1ns/1ps
module tb_vram_credit_timing;
    reg src_clk=0,mem_clk=0,reset_async=1,backend_enable=0;
    always #3 src_clk=~src_clk;
    always #8 mem_clk=~mem_clk;
    reg rd=0,we=0;reg [27:0] addr=0;reg [7:0] count=0;
    wire busy,fault,ready,mem_valid,mem_write;wire [63:0] dout;
    wire [23:0] mem_addr;wire [31:0] mem_data;wire [3:0] mem_mask;
    psx_vram_stream dut (
        .src_clk(src_clk),.mem_clk(mem_clk),.reset_async(reset_async),.mem_initialized(1'b1),
        .rd(rd),.we(we),.addr(addr),.count(count),.be(8'hff),.din(64'h12345678_89abcdef),
        .busy(busy),.fault(fault),.ready(ready),.dout(dout),
        .mem_valid(mem_valid),.mem_ready(backend_enable),.mem_write(mem_write),.mem_addr(mem_addr),
        .mem_wdata(mem_data),.mem_byte_en(mem_mask),.mem_rvalid(1'b0),.mem_rdata(32'b0));
    integer physical_words=0,address_checks=0,accepted=0;
    always @(posedge src_clk) if(!dut.src_reset && we && !busy) accepted++;
    always @(posedge mem_clk) if(mem_valid && backend_enable) begin
        if(!mem_write || physical_words>=1020 ||
           mem_addr!==(physical_words<510 ? 24'h400+24'(physical_words):24'h800+24'(physical_words-510)) ||
           mem_data!==(physical_words%2 ? 32'h12345678:32'h89abcdef) || mem_mask!==4'hf)
            $fatal(1,"VRAM conservative credit physical order/data mismatch");
        physical_words++;
    end
    task send_write(input [27:0] a);
        @(negedge src_clk);addr=a;count=255;we=1;
        @(posedge src_clk);while(busy) @(posedge src_clk);
        @(negedge src_clk);we=0;
    endtask
    initial begin
        repeat(8) @(negedge src_clk);reset_async=0;
        repeat(20) @(negedge src_clk);
        send_write(28'h1000);send_write(28'h2000);
        // Two 255-beat writes reserve1020 words while the backend is stopped.
        // Every address must observe the same capacity stall, including an
        // invalid descriptor. No source edge occurs during this input sweep.
        @(negedge src_clk);we=1;count=255;addr=28'h4000000;
        #0.1;
        if(!busy) $fatal(1,"VRAM BUSY depends on invalid address instead of conservative credit");
        for(integer bit_index=0;bit_index<28;bit_index++) begin
            addr=28'b1<<bit_index;#0.01;
            if(!busy) $fatal(1,"VRAM BUSY address noninterference failure");
            address_checks++;
        end
        addr=28'h4000000;
        repeat(20) @(negedge src_clk);
        if(fault || physical_words!=0 || accepted!=2)
            $fatal(1,"VRAM invalid command accepted before credits drained");
        backend_enable=1;
        @(posedge src_clk);while(busy) @(posedge src_clk);
        @(negedge src_clk);we=0;
        wait(fault);wait(physical_words==1020);
        repeat(50) @(negedge src_clk);
        if(physical_words!=1020 || accepted!=3 || ready)
            $fatal(1,"VRAM malformed write escaped rejection");
        $display("PASS VRAM conservative credit: address_checks=%0d words=%0d accepted=%0d invalid_safe",address_checks,physical_words,accepted);
        $finish;
    end
    initial begin #1000000;$fatal(1,"VRAM conservative credit watchdog");end
endmodule
