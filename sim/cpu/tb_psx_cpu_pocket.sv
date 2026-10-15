`timescale 1ns/1ps
`default_nettype none
module tb_psx_cpu_pocket;
    parameter integer CORE_HALF=10;
    reg clk_74a=0,clk_core=0,reset_async=1,run=0;
    always #6.734 clk_74a=!clk_74a;
    initial begin #3;forever #(CORE_HALF)clk_core=!clk_core;end
    reg [5:0] irq=0;
    wire core_reset,host_clk,host_reset,initialized,protocol_error,unsupported_cache_mode;
    reg host_valid=0,host_write=0,host_rsp_ready=0;
    wire host_ready,host_rsp_valid,host_rsp_error;
    reg [24:0] host_addr=0;
    reg [3:0] host_halfwords=2;
    reg [127:0] host_wdata=0;
    reg [15:0] host_byte_en=15;
    wire [127:0] host_rsp_rdata;
    wire io_valid,io_write,io_rsp_ready;
    wire [31:0] io_addr,io_wdata,io_store_raw;
    wire [3:0] io_byte_en;
    wire [1:0] io_size;
    reg io_rsp_valid=0,io_rsp_error=0;
    reg [31:0] io_rsp_rdata=0;
    wire io_ready=!io_rsp_valid && !core_reset;
    wire cop2_valid,cop2_rsp_ready;
    reg cop2_ready=1,cop2_rsp_valid=0,cop2_rsp_error=0;
    reg [31:0] cop2_rsp_rdata=0;
    wire [2:0] cop2_kind;
    wire [4:0] cop2_index;
    wire [24:0] cop2_command;
    wire [31:0] cop2_wdata;
    wire trace_valid,trace_exception;
    wire [31:0] trace_pc,trace_instruction;
    wire [4:0] trace_exception_code;
    reg [4:0] debug_reg=0;
    wire [31:0] debug_value,debug_pc,debug_hi,debug_lo,debug_status,debug_cause,debug_epc,debug_badaddr;
    wire [12:0] dram_a;
    wire [1:0] dram_ba,dram_dqm;
    wire dram_ras_n,dram_cas_n,dram_we_n,dram_cke,dram_clk;
    wire [15:0] dram_dq;
    psx_cpu_pocket #(.T_INIT(12),.T_REFI(37)) dut(.*);
    sdr_pin_model #(.T_AC(5.5),.T_AC_SECOND(2.5),.T_HZ(6.0)) chip(
        .clk(dram_clk),.cke(dram_cke),.ras_n(dram_ras_n),.cas_n(dram_cas_n),.we_n(dram_we_n),
        .address(dram_a),.bank(dram_ba),.dm(dram_dqm),.dq(dram_dq),.host_oe(dut.memory.pads.phy.oe_reg)
    );
    reg done=0,dma_write_seen=0;
    integer retired=0,peripheral_reads=0;
    always @(posedge clk_core) begin
        if(core_reset)begin
            io_rsp_valid<=0;io_rsp_error<=0;io_rsp_rdata<=0;done<=0;dma_write_seen<=0;
            retired=0;peripheral_reads=0;
        end
        else begin
            if(protocol_error || unsupported_cache_mode || trace_exception || cop2_valid)
                $fatal(1,"Unexpected CPU/memory diagnostic in synthetic reset program");
            if(trace_valid)retired=retired+1;
            if(io_rsp_valid && io_rsp_ready)io_rsp_valid<=0;
            if(io_valid && io_ready)begin
                io_rsp_valid<=1;io_rsp_error<=0;io_rsp_rdata<=0;
                if(io_write && io_addr==32'h1F801080)begin
                    if(io_size!=1 || io_byte_en!=3 || io_store_raw!=32'hDEADBEEF)$fatal(1,"Narrow MMIO lost full register source value");
                    dma_write_seen<=1;
                end else if(!io_write && io_addr==32'h1F801070)begin
                    io_rsp_rdata<=32'h0BADF00D;peripheral_reads=peripheral_reads+1;
                end else if(io_write && io_addr==32'h1F802040 && io_store_raw==32'hD00DFEED)done<=1;
                else $fatal(1,"Unexpected synthetic peripheral request %h",io_addr);
            end
        end
    end
    task automatic host_access(input reg write_flag,input reg [24:0] address,input reg [31:0] data,input reg [31:0] expected);
        begin
            @(negedge host_clk);host_valid=1;host_write=write_flag;host_addr=address;host_wdata={96'b0,data};host_rsp_ready=0;
            #0.1;while(!host_ready)begin @(negedge host_clk);#0.1;end
            @(negedge host_clk);host_valid=0;
            while(!host_rsp_valid)@(negedge host_clk);
            if(host_rsp_error || host_rsp_rdata[31:0]!==expected || host_rsp_rdata[127:32]!=0)
                $fatal(1,"Host readback mismatch address=%h got=%h expected=%h",address,host_rsp_rdata,expected);
            host_rsp_ready=1;@(negedge host_clk);host_rsp_ready=0;
        end
    endtask
    integer file,fields,words;
    reg [31:0] done_pc,rom_first,word_data;
    reg [24:0] load_address;
    reg [31:0] expected_regs [0:31];
    reg [8191:0] directory;
    string filename;
    integer reads_before_abort;
    task automatic reset_component;
        begin
            reset_async=1;run=0;
            repeat(6)@(negedge clk_core);reset_async=0;
            while(core_reset || host_reset || !initialized)@(negedge clk_core);
            repeat(5)@(negedge clk_core);
            if(trace_valid || host_rsp_valid || io_valid || done)$fatal(1,"Stale CPU/host state after component reset");
        end
    endtask
    initial begin
        if(!$value$plusargs("programs=%s",directory))$fatal(1,"Missing smoke program directory");
        filename=$sformatf("%0s/smoke.txt",directory);file=$fopen(filename,"r");
        if(!file)$fatal(1,"Missing synthetic ROM");
        fields=$fscanf(file,"%d %h %h\n",words,done_pc,rom_first);
        if(fields!=3)$fatal(1,"Bad smoke header");
        filename=$sformatf("%0s/smoke-regs.hex",directory);$readmemh(filename,expected_regs);
        repeat(5)@(negedge clk_core);reset_async=0;
        while(core_reset || host_reset || !initialized)@(negedge clk_core);
        // Use the actual second memory client to populate the synthetic BIOS.
        for(integer word_index=0;word_index<words;word_index=word_index+1)begin
            fields=$fscanf(file,"%h %h\n",load_address,word_data);
            if(fields!=2)$fatal(1,"Bad smoke ROM word");
            host_access(1,load_address,word_data,0);
        end
        $fclose(file);
        for(integer boot=0;boot<3;boot=boot+1)begin
            run=1;wait(trace_valid);
            host_access(0,25'h800000,32'b0,32'h0BF00100); // Debug reads can coexist with CPU execution.
            wait(done);@(negedge clk_core);run=0;
            while(!(trace_valid && trace_pc==done_pc))@(negedge clk_core);
            repeat(3)@(negedge clk_core);
            if(retired!=words || !dma_write_seen || peripheral_reads!=1)$fatal(1,"Synthetic program completion/trace mismatch retired=%0d words=%0d",retired,words);
            for(integer regnum=0;regnum<32;regnum=regnum+1)begin
                debug_reg=5'(regnum);#0.02;
                if(debug_value!==expected_regs[regnum])$fatal(1,"Pocket CPU GPR=%0d got=%h expected=%h",regnum,debug_value,expected_regs[regnum]);
            end
            host_access(0,25'h800,32'b0,32'h44552233);
            host_access(0,25'h800200,32'b0,rom_first); // CPU BIOS write was ignored.
            if(boot<2)begin
                reset_component();
                if(boot==1)begin
                    reads_before_abort=chip.reads;run=1;
                    wait(chip.reads>reads_before_abort);#1;
                    reset_component(); // Abort a real SDRAM fetch, then reuse the retained ROM.
                end
            end
        end
        if(!done || protocol_error || unsupported_cache_mode)$fatal(1,"Pocket CPU completion failed");
        $display("PASS CPU pins core_half=%0d boots=3 retired_per_boot=%0d SDRAM_reads=%0d SDRAM_writes=%0d",CORE_HALF,retired,chip.reads,chip.writes);
        $finish;
    end
    initial begin #5000000;$fatal(1,"Pocket CPU smoke timeout");end
endmodule
`default_nettype wire
