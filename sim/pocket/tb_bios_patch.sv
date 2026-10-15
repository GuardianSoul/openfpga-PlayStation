// psx_bios_patch: loader writes pass through; with skip on, the five shell
// words land at word 0x6000 after host_run and w_start bios_ready; with skip
// off nothing extra is written; bios_ready never precedes host_run; a reload
// passes loader writes through again and repeats the patch.
`timescale 1ns/1ps
module tb_bios_patch;
    reg clk=0; always #5 clk=~clk;
    reg reset=1, loader_ready=0, host_run=0, skip=0;
    reg l_valid=0; reg [24:0] l_addr=0; reg [127:0] l_data=0;
    wire l_ready, l_rsp, l_error, bios_ready, patched;
    wire m_valid, m_write, m_rsp_ready; wire [24:0] m_addr; wire [3:0] m_halfwords; wire [127:0] m_data; wire [15:0] m_be;
    reg m_ready=0, m_rsp=0;
    psx_bios_patch #(.SETTLE(16)) dut(.clk(clk),.reset(reset),.loader_ready(loader_ready),.host_run(host_run),.skip(skip),
        .bios_ready(bios_ready),.patched(patched),.l_valid(l_valid),.l_write(1'b1),.l_ready(l_ready),.l_addr(l_addr),
        .l_halfwords(4'd2),.l_data(l_data),.l_be(16'h000f),.l_rsp(l_rsp),.l_error(l_error),.l_rsp_ready(1'b1),
        .m_valid(m_valid),.m_write(m_write),.m_ready(m_ready),.m_addr(m_addr),.m_halfwords(m_halfwords),.m_data(m_data),
        .m_be(m_be),.m_rsp(m_rsp),.m_error(1'b0),.m_rsp_ready(m_rsp_ready));
    // Memory: word-addressed sparse store, random ready and response delays.
    reg [31:0] mem[0:32767]; integer writes=0, errs=0, pending=0, delay=0;
    reg host_run_q=0;
    always @(posedge clk) begin
        m_rsp<=0;
        if (pending && delay==0) begin m_rsp<=1; pending<=0; end
        else if (delay) delay<=delay-1;
        m_ready<=!pending && !m_rsp && ($urandom%3!=0);
        if (m_valid && m_ready && !pending) begin
            for (integer h=0; h<m_halfwords; h=h+2) if (m_be[h*2+:4]==4'hf) mem[(m_addr[16:1]+h/2)&32767]<=m_data[h*16+:32];
            writes<=writes+1; pending<=1; delay<=$urandom%5;
        end
        // bios_ready is registered: it may lag a falling host_run by one clock
        // (psx_boot_gate also takes host_run directly).
        host_run_q<=host_run;
        if (bios_ready && !host_run && !host_run_q) begin errs=errs+1; $display("bios_ready w_start host_run"); end
    end
    task automatic load(input integer n);
        for (integer i=0;i<n;i=i+1) begin
            @(negedge clk); l_valid=1; l_addr={7'b0,i[16:0],1'b0}; l_data={96'b0,32'h1000_0000+i};
            while (!(l_valid && l_ready)) @(negedge clk);
            @(negedge clk); l_valid=0;
            while (!l_rsp) @(negedge clk);
        end
    endtask
    task automatic check_patch(input reg on);
        if (on) begin
            if (mem[16'h6000]!==32'h3C011F80 || mem[16'h6001]!==32'h3C0A0300 || mem[16'h6002]!==32'hAC2A1814 ||
                mem[16'h6003]!==32'h03E00008 || mem[16'h6004]!==32'h0 || !patched) begin
                errs=errs+1; $display("patch words %h %h %h %h %h patched=%0d",mem[16'h6000],mem[16'h6001],mem[16'h6002],mem[16'h6003],mem[16'h6004],patched);
            end
        end else if (mem[16'h6000]!==32'hdeadbeef || patched) begin errs=errs+1; $display("unexpected patch %h",mem[16'h6000]); end
    endtask
    integer w_start;
    initial begin
        for (integer i=0;i<32768;i=i+1) mem[i]=32'hdeadbeef;
        repeat(3) @(negedge clk); reset=0;
        // Skip off: no extra writes, ready after host_run.
        load(6); if (mem[3]!==32'h1000_0003) begin errs=errs+1; $display("passthrough"); end
        w_start=writes; loader_ready=1; repeat(20) @(negedge clk);
        if (bios_ready) begin errs=errs+1; $display("ready without host_run"); end
        host_run=1; repeat(25) @(negedge clk);
        if (!bios_ready || writes!=w_start) begin errs=errs+1; $display("skip off: ready=%0d writes=%0d",bios_ready,writes-w_start); end
        check_patch(0);
        // Reload with skip on (value written w_start Reset Exit).
        // The Pocket may write the setting just after Reset Exit (within SETTLE).
        loader_ready=0; host_run=0; skip=0; repeat(3) @(negedge clk);
        load(4); w_start=writes; loader_ready=1; repeat(10) @(negedge clk);
        host_run=1; repeat(4) @(negedge clk); skip=1;
        while (!bios_ready) begin @(negedge clk); if (writes>w_start+2) begin errs=errs+1; $display("too many writes"); end end
        if (writes!=w_start+2) begin errs=errs+1; $display("patch writes %0d",writes-w_start); end
        check_patch(1);
        // Reload again: loader writes pass, patch repeats.
        loader_ready=0; repeat(3) @(negedge clk); load(3); w_start=writes; loader_ready=1;
        while (!bios_ready) @(negedge clk);
        if (writes!=w_start+2) begin errs=errs+1; $display("second patch writes %0d",writes-w_start); end
        $display("errs=%0d", errs); $finish;
    end
    initial begin #2000000; $display("errs=timeout"); $finish; end
endmodule
