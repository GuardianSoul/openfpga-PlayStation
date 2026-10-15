`timescale 1ns/1ps
`default_nettype none

// Bounded reset diagnostic using the actual CPU and guest decoder. External
// RAM/ROM here are a functional bus fixture, not the SDRAM pin model. Stop at
// the first unsupported device; never fabricate its response to advance boot.
module tb_psx_bios;
    reg clk=0, rst=1, run=0;
    always #5 clk=!clk;
    reg [5:0] irq=0;
    wire bus_valid, bus_ready, bus_write, bus_fetch, bus_rsp_valid, bus_rsp_ready, bus_rsp_error, cache_isolated;
    wire [31:0] bus_addr, bus_wdata, bus_store_raw, bus_rsp_rdata;
    wire [3:0] bus_byte_en;
    wire [1:0] bus_size;
    wire mem_valid, mem_ready, mem_write, mem_rsp_ready;
    wire [24:0] mem_addr;
    wire [3:0] mem_halfwords;
    wire [127:0] mem_wdata;
    wire [15:0] mem_byte_en;
    reg mem_rsp_valid=0, mem_rsp_error=0;
    reg [127:0] mem_rsp_rdata=0;
    wire io_valid, io_write, io_rsp_ready;
    wire [31:0] io_addr, io_wdata, io_store_raw;
    wire [3:0] io_byte_en;
    wire [1:0] io_size;
    wire io_ready=0, io_rsp_valid=0, io_rsp_error=0;
    wire [31:0] io_rsp_rdata=0;
    wire cop2_valid, cop2_rsp_ready;
    wire [2:0] cop2_kind;
    wire [4:0] cop2_index;
    wire [24:0] cop2_command;
    wire [31:0] cop2_wdata;
    wire cop2_ready=0, cop2_rsp_valid=0, cop2_rsp_error=0;
    wire [31:0] cop2_rsp_rdata=0;
    wire trace_valid, trace_exception, unsupported_cache_mode;
    wire [31:0] trace_pc, trace_instruction;
    wire [4:0] trace_exception_code;
    reg [4:0] debug_reg=0;
    wire [31:0] debug_value, debug_pc, debug_hi, debug_lo, debug_status, debug_cause, debug_epc, debug_badaddr;
    psx_cpu cpu(.*);
    psx_cpu_bus guest(.*);

    reg [31:0] ram [0:524287];
    reg [31:0] bios [0:131071];
    reg pending=0;
    integer delay_cycles=0, cycles=0, retired=0, requests=0;
    reg [31:0] rng=32'h50535808;
    assign mem_ready=!rst && !pending && !mem_rsp_valid && rng[0];
    always @(posedge clk) begin
        if(rst)begin pending<=0; mem_rsp_valid<=0; end
        else begin
            rng<=rng*32'd1664525+32'd1013904223;
            if(mem_rsp_valid && mem_rsp_ready)mem_rsp_valid<=0;
            if(mem_valid && mem_ready)begin : transfer
                integer index;
                reg is_bios, valid_address;
                if(pending || mem_rsp_valid || mem_halfwords!=2 || mem_addr[0])
                    $fatal(1,"Invalid diagnostic memory transaction");
                is_bios=mem_addr>=25'h800000 && mem_addr<25'h840000;
                valid_address=is_bios || mem_addr<25'h100000;
                index=is_bios ? integer'((mem_addr-25'h800000)>>1) : integer'(mem_addr>>1);
                mem_rsp_error<=!valid_address;
                mem_rsp_rdata<=!valid_address || mem_write ? 128'b0 : {96'b0,is_bios ? bios[index] : ram[index]};
                if(mem_write && valid_address)begin
                    if(is_bios)$fatal(1,"Guest decoder forwarded a BIOS write");
                    for(integer lane=0;lane<4;lane=lane+1)
                        if(mem_byte_en[lane])ram[index][lane*8 +:8]<=mem_wdata[lane*8 +:8];
                end
                pending<=1; delay_cycles<=1+integer'(rng[4:1]); requests=requests+1;
            end
            if(pending)begin
                if(delay_cycles==0)begin pending<=0;mem_rsp_valid<=1;end
                else delay_cycles<=delay_cycles-1;
            end
        end
    end

    string bios_path, trace_path;
    integer trace_file, max_instructions=100000, max_cycles=5000000;
    task automatic stop_diagnostic(input string reason, input reg [31:0] pc, instruction);
        begin
            $display("BIOS_DIAGNOSTIC {\"stop_reason\":\"%s\",\"retired\":%0d,\"cycles\":%0d,\"memory_requests\":%0d,\"pc\":\"%08h\",\"instruction\":\"%08h\",\"status\":\"%08h\",\"exception_code\":%0d,\"io_write\":%0d,\"io_address\":\"%08h\",\"io_raw_data\":\"%08h\",\"io_lane_data\":\"%08h\",\"io_mask\":%0d,\"io_size\":%0d}",
                reason,retired,cycles,requests,pc,instruction,debug_status,trace_exception_code,
                io_write,io_addr,io_store_raw,io_wdata,io_byte_en,io_size);
            $fclose(trace_file);
            $finish;
        end
    endtask
    initial begin
        if(!$value$plusargs("bios=%s",bios_path) || !$value$plusargs("trace=%s",trace_path))
            $fatal(1,"Missing local BIOS/trace paths");
        if($value$plusargs("max_instructions=%d",max_instructions))begin end
        if($value$plusargs("max_cycles=%d",max_cycles))begin end
        if(max_instructions<=0 || max_cycles<=0)$fatal(1,"Diagnostic bounds must be positive");
        $readmemh(bios_path,bios);
        // RAM power-on data is a deterministic zero fixture, not a PSX guarantee.
        for(integer index=0;index<524288;index=index+1)ram[index]=0;
        trace_file=$fopen(trace_path,"w");
        if(!trace_file)$fatal(1,"Could not create local trace");
        $fdisplay(trace_file,"retired,pc,instruction,exception,code,next_pc,status,cause,epc,badaddr");
        repeat(3)@(negedge clk);rst=0;run=1;
        forever begin
            @(negedge clk);cycles=cycles+1;
            if(trace_valid)begin
                $fdisplay(trace_file,"%0d,%08h,%08h,%0d,%0d,%08h,%08h,%08h,%08h,%08h",
                    retired,trace_pc,trace_instruction,trace_exception,trace_exception_code,
                    debug_pc,debug_status,debug_cause,debug_epc,debug_badaddr);
                if(trace_exception)stop_diagnostic("exception",trace_pc,trace_instruction);
                retired=retired+1;
            end
            if(io_valid)stop_diagnostic("mmio",debug_pc,cpu.instruction);
            if(cop2_valid)stop_diagnostic("cop2",debug_pc,cpu.instruction);
            if(unsupported_cache_mode)stop_diagnostic("isolated_cache",debug_pc,cpu.instruction);
            if(retired>=max_instructions)stop_diagnostic("instruction_limit",debug_pc,cpu.instruction);
            if(cycles>=max_cycles)stop_diagnostic("cycle_limit",debug_pc,cpu.instruction);
        end
    end
endmodule
`default_nettype wire
