`timescale 1ns/1ps
`default_nettype none
module tb_psx_cpu;
    parameter [31:0] SEED = 32'h50535808;
    reg clk=0, rst=1, run=0;
    always #5 clk=!clk;
    reg [5:0] irq=0;
    wire bus_valid, bus_write, bus_fetch, bus_rsp_ready, cop2_valid, cop2_rsp_ready, cache_isolated;
    wire [31:0] bus_addr, bus_wdata, bus_store_raw, cop2_wdata;
    wire [3:0] bus_byte_en;
    wire [1:0] bus_size;
    wire [2:0] cop2_kind;
    wire [4:0] cop2_index;
    wire [24:0] cop2_command;
    reg bus_ready=0, bus_rsp_valid=0, bus_rsp_error=0;
    reg [31:0] bus_rsp_rdata=0;
    reg cop2_ready=0, cop2_rsp_valid=0, cop2_rsp_error=0;
    reg [31:0] cop2_rsp_rdata=0;
    wire trace_valid, trace_exception;
    wire [31:0] trace_pc, trace_instruction;
    wire [4:0] trace_exception_code;
    reg [4:0] debug_reg=0;
    wire [31:0] debug_value, debug_pc, debug_hi, debug_lo, debug_status, debug_cause, debug_epc, debug_badaddr;
    psx_cpu dut (.*);
    reg [31:0] memory [0:20479];
    reg [31:0] gte_fixture [0:1][0:31];
    reg bus_pending=0, cop_pending=0;
    integer bus_delay=0, cop_delay=0;
    reg [31:0] rng=SEED;
    reg bus_stalled=0, cop_stalled=0;
    reg [103:0] bus_held;
    reg [64:0] cop_held;
    reg block_requests=0, block_responses=0;
    integer bus_requests=0, cop_requests=0, exceptions=0, instructions=0;
    function automatic integer slot(input reg [31:0] address);
        reg [31:0] physical;
        begin
            physical=address & 32'h1FFFFFFF;
            if (physical < 32'h10000) slot=integer'(physical>>2);
            else if (physical >= 32'h1FC00000 && physical < 32'h1FC04000) slot=16384+integer'((physical-32'h1FC00000)>>2);
            else slot=-1;
        end
    endfunction
    function automatic [7:0] initial_byte(input integer address);
        initial_byte=8'(address*17+(address>>8)*23+'h5A);
    endfunction
    always @(posedge clk) begin
        if (rst) begin
            bus_ready<=0; bus_pending<=0; bus_rsp_valid<=0; bus_stalled<=0;
            cop2_ready<=0; cop_pending<=0; cop2_rsp_valid<=0; cop_stalled<=0;
            for (integer bank=0;bank<2;bank=bank+1)
                for (integer regnum=0;regnum<32;regnum=regnum+1) gte_fixture[bank][regnum]<=0;
        end else begin
            rng<=rng*32'd1664525+32'd1013904223;
            bus_ready<=!block_requests && !bus_pending && !bus_rsp_valid && rng[0];
            cop2_ready<=!cop_pending && !cop2_rsp_valid && rng[3];
            if (bus_stalled && (!bus_valid || {bus_fetch,bus_write,bus_addr,bus_wdata,bus_store_raw,bus_byte_en,bus_size} !== bus_held))
                $fatal(1,"CPU bus request changed while stalled");
            bus_stalled<=bus_valid && !bus_ready;
            bus_held<={bus_fetch,bus_write,bus_addr,bus_wdata,bus_store_raw,bus_byte_en,bus_size};
            if (cop_stalled && (!cop2_valid || {cop2_kind,cop2_index,cop2_command,cop2_wdata} !== cop_held))
                $fatal(1,"CPU coprocessor request changed while stalled");
            cop_stalled<=cop2_valid && !cop2_ready;
            cop_held<={cop2_kind,cop2_index,cop2_command,cop2_wdata};
            if (bus_valid && bus_ready) begin : accept_bus
                integer location;
                reg error;
                location=slot(bus_addr); error=location<0 || (bus_addr & 32'h1FFFFFFC)==32'hFFFC;
                if (bus_pending || bus_rsp_valid) $fatal(1,"Duplicate outstanding CPU bus request");
                bus_requests=bus_requests+1; bus_pending<=1; bus_delay<=2+integer'(rng[7:4]); bus_ready<=0;
                bus_rsp_error<=error; bus_rsp_rdata<=error ? 32'b0 : memory[location];
                if (bus_write && !error) begin
                    for (integer lane=0;lane<4;lane=lane+1)
                        if (bus_byte_en[lane]) memory[location][lane*8 +:8]<=bus_wdata[lane*8 +:8];
                end
            end
            if (bus_pending && !block_responses) begin
                if (bus_delay==0) begin bus_pending<=0; bus_rsp_valid<=1; end
                else bus_delay<=bus_delay-1;
            end
            if (bus_rsp_valid && bus_rsp_ready) bus_rsp_valid<=0;
            if (cop2_valid && cop2_ready) begin
                if (cop_pending || cop2_rsp_valid) $fatal(1,"Duplicate outstanding coprocessor request");
                cop_requests=cop_requests+1; cop_pending<=1; cop_delay<=1+integer'(rng[11:8]); cop2_ready<=0;
                cop2_rsp_error<=cop2_kind==4 && cop2_command==25'h1FFFFFF;
                case (cop2_kind)
                    0: cop2_rsp_rdata<=gte_fixture[0][cop2_index];
                    1: cop2_rsp_rdata<=gte_fixture[1][cop2_index];
                    2: begin gte_fixture[0][cop2_index]<=cop2_wdata; cop2_rsp_rdata<=0; end
                    3: begin gte_fixture[1][cop2_index]<=cop2_wdata; cop2_rsp_rdata<=0; end
                    4: begin
                        if (cop2_command!=25'h1FFFFFF) gte_fixture[0][31]<={7'b0,cop2_command} ^ 32'hA5A5A5A5;
                        cop2_rsp_rdata<=0;
                    end
                    default: $fatal(1,"Unknown coprocessor fixture operation");
                endcase
            end
            if (cop_pending) begin
                if (cop_delay==0) begin cop_pending<=0; cop2_rsp_valid<=1; end
                else cop_delay<=cop_delay-1;
            end
            if (cop2_rsp_valid && cop2_rsp_ready) cop2_rsp_valid<=0;
        end
    end
    integer index_file, trace_file, fields, count, case_count=0, expected_total=0;
    reg [8191:0] directory;
    reg [31:0] expected [0:43];
    string stem, label, filename;
    task automatic reset_cpu;
        begin
            @(negedge clk); rst=1; run=0; irq=0;
            #0.1;
            if (bus_valid || bus_rsp_ready || cop2_valid || cop2_rsp_ready) $fatal(1,"CPU transfer not gated by reset");
            repeat (3) @(negedge clk); rst=0;
            @(negedge clk);
            if (debug_pc!==32'hBFC00000 || debug_status!==32'h10900000 || debug_hi || debug_lo)
                $fatal(1,"CPU reset state mismatch");
        end
    endtask
    initial begin
        if (!$value$plusargs("programs=%s",directory)) $fatal(1,"Missing CPU programs");
        filename=$sformatf("%0s/index.txt",directory); index_file=$fopen(filename,"r");
        if (!index_file) $fatal(1,"Missing CPU program index");
        while (!$feof(index_file)) begin
            fields=$fscanf(index_file,"%s %s %d\n",stem,label,count);
            if (fields!=3) $fatal(1,"Bad CPU index");
            rst=1; run=0;
            for (integer location=0;location<20480;location=location+1) begin
                for (integer lane=0;lane<4;lane=lane+1)
                    memory[location][lane*8 +:8]=initial_byte(location*4+lane);
            end
            filename=$sformatf("%0s/%0s.hex",directory,stem); $readmemh(filename,memory);
            filename=$sformatf("%0s/%0s.trace",directory,stem); trace_file=$fopen(filename,"r");
            if (!trace_file) $fatal(1,"Missing CPU trace");
            reset_cpu();
            for (integer step=0;step<count;step=step+1) begin
                for (integer field=0;field<44;field=field+1) begin
                    fields=$fscanf(trace_file,"%h",expected[field]);
                    if (fields!=1) $fatal(1,"Bad architectural trace");
                end
                @(negedge clk); irq=expected[0][5:0]; run=1;
                while (!trace_valid) @(negedge clk);
                run=0; #0.1;
                if ({trace_pc,trace_instruction,trace_exception,trace_exception_code,debug_pc,debug_hi,debug_lo,
                     debug_status,debug_cause,debug_epc,debug_badaddr} !==
                    {expected[1],expected[2],expected[3][0],expected[4][4:0],expected[5],expected[6],expected[7],
                     expected[8],expected[9],expected[10],expected[11]}) begin
                    $display("case=%s step=%0d pc=%h instruction=%h exception=%b code=%0d next=%h hi=%h lo=%h sr=%h cause=%h epc=%h bad=%h",
                        label,step,trace_pc,trace_instruction,trace_exception,trace_exception_code,debug_pc,debug_hi,debug_lo,debug_status,debug_cause,debug_epc,debug_badaddr);
                    $display("expected pc=%h instruction=%h exception=%h code=%h next=%h hi=%h lo=%h sr=%h cause=%h epc=%h bad=%h",
                        expected[1],expected[2],expected[3],expected[4],expected[5],expected[6],expected[7],expected[8],expected[9],expected[10],expected[11]);
                    $fatal(1,"CPU architectural state mismatch");
                end
                for (integer regnum=0;regnum<32;regnum=regnum+1) begin
                    debug_reg=5'(regnum); #0.02;
                    if (debug_value!==expected[12+regnum]) $fatal(1,"case=%s step=%0d GPR=%0d got=%h expected=%h",label,step,regnum,debug_value,expected[12+regnum]);
                end
                instructions=instructions+1; exceptions=exceptions+integer'(trace_exception);
            end
            $fclose(trace_file); case_count=case_count+1; expected_total=expected_total+count;
        end
        $fclose(index_file);
        // Pausing stops at an instruction boundary; accepted work still drains.
        reset_cpu(); block_responses=1; run=1;
        wait(bus_pending); @(negedge clk); run=0; block_responses=0;
        while (!trace_valid) @(negedge clk);
        if (debug_pc!==32'hBFC00004) $fatal(1,"In-flight instruction failed to finish while paused");
        repeat (10) @(negedge clk);
        if (bus_valid || trace_valid) $fatal(1,"Paused CPU fetched another instruction");
        // A common reset discards pending requests and the backend's old reply.
        reset_cpu(); block_responses=1; run=1;
        wait(bus_pending); reset_cpu(); block_responses=0;
        repeat (10) @(negedge clk);
        if (trace_valid || bus_rsp_valid) $fatal(1,"Stale instruction after reset");
        reset_cpu(); block_requests=1; run=1;
        wait(bus_valid); reset_cpu(); block_requests=0;
        run=1; while (!trace_valid) @(negedge clk); run=0;
        if (trace_pc!==32'hBFC00000 || debug_pc!==32'hBFC00004) $fatal(1,"CPU failed to restart after stalled request reset");
        if (instructions!=expected_total || cop_requests==0 || exceptions<10) $fatal(1,"CPU coverage/accounting missing");
        $display("PASS CPU seed=%h programs=%0d retired=%0d exceptions=%0d bus_requests=%0d cop2_requests=%0d",SEED,case_count,instructions,exceptions,bus_requests,cop_requests);
        $finish;
    end
    initial begin #10000000; $fatal(1,"CPU test timeout"); end
endmodule
`default_nettype wire
