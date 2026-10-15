// SPDX-License-Identifier: MIT
// MiSTer's held 64-bit DDR commands onto an ordered 32-bit memory port.
// Address is bytes, COUNT is 64-bit beats (1..255); writes repeat DIN/BE.
// RD/WE and payload are held until an edge with BUSY=0 (acceptance). Writes
// are posted, reads fence preceding writes; no accepted command is replayed.
// Reset also flushes the backend pipeline. No guest feature is specialized.
// Both FIFOs reuse the pinned Laxer dual_clock_fifo (BSD, notice in that file).
`default_nettype none
module psx_vram_stream #(
    parameter integer FIFO_AW=4, MAX_WRITE_WORDS=1024
)(
    input wire src_clk, mem_clk, reset_async, mem_initialized,
    input wire rd, we,
    input wire [27:0] addr,
    input wire [7:0] count, be,
    input wire [63:0] din,
    output wire busy, fault,
    output reg ready=0,
    output wire [63:0] dout,
    output wire mem_valid, mem_write,
    input wire mem_ready,
    output wire [23:0] mem_addr,
    output wire [31:0] mem_wdata,
    output wire [3:0] mem_byte_en,
    input wire mem_rvalid,
    input wire [31:0] mem_rdata
);
    localparam integer PB=FIFO_AW+1, WB=PB+1, TXB=$clog2(MAX_WRITE_WORDS*2);
    localparam integer CREDIT_WORDS=2*((1<<FIFO_AW)-1);
    localparam IDLE=0, READ=1, BAD=2;
    (* async_reg="true" *) reg [2:0] src_reset_pipe=7, mem_reset_pipe=7;
    always @(posedge src_clk or posedge reset_async)
        if(reset_async) src_reset_pipe<=7;
        else src_reset_pipe<={src_reset_pipe[1:0],1'b0};
    always @(posedge mem_clk or posedge reset_async)
        if(reset_async) mem_reset_pipe<=7;
        else mem_reset_pipe<={mem_reset_pipe[1:0],1'b0};
    wire src_reset=src_reset_pipe[2], mem_reset=mem_reset_pipe[2];
    function automatic [PB-1:0] pair_binary(input [PB-1:0] gray);
        reg [PB-1:0] value;
        begin
            value[PB-1]=gray[PB-1];
            for(integer i=PB-2;i>=0;i=i-1) value[i]=value[i+1]^gray[i];
            pair_binary=value;
        end
    endfunction
    function automatic [TXB-1:0] write_binary(input [TXB-1:0] gray);
        reg [TXB-1:0] value;
        begin
            value[TXB-1]=gray[TXB-1];
            for(integer i=TXB-2;i>=0;i=i-1) value[i]=value[i+1]^gray[i];
            write_binary=value;
        end
    endfunction
    reg [1:0] src_state=IDLE;
    (* async_reg="true" *) reg [1:0] initialized_sync=0, backend_fault_sync=0;
    reg input_fault=0, backend_fault=0, source_online=0;
    // The reused FIFO resets pointers on local clock edges. Do not let the
    // destination inspect stale source pointers while that clock is stopped.
    (* async_reg="true" *) reg [1:0] source_online_sync=0;
    reg [7:0] src_remaining=0;
    reg bad_read=0, bad_response=0;
    wire [28:0] byte_end={1'b0,addr}+{18'b0,count,3'b0};
    wire invalid_command=(rd && we) || count==0 || addr[2:0]!=0 ||
        addr[27:26]!=0 || byte_end>29'h04000000;
    wire cmd_empty, cmd_full, fifo_empty, fifo_full;
    wire [104:0] cmd_fifo_data;
    wire [63:0] fifo_data;
    reg [PB-1:0] command_sequence=0, retired_commands=0, retired_command_gray=0;
    (* async_reg="true" *) reg [PB-1:0] retired_command_meta=0, retired_command_sync=0;
    reg [TXB-1:0] posted_words=0, finished_words=0, finished_word_gray=0;
    (* async_reg="true" *) reg [TXB-1:0] finished_word_meta=0, finished_word_sync=0;
    wire [PB-1:0] pending_commands=command_sequence-pair_binary(retired_command_sync);
    wire [TXB-1:0] pending_writes=posted_words-write_binary(finished_word_sync);
    // The native producer may present a command before BUSY falls, then
    // replaces or clears it only on the accepting edge. Capacity includes
    // this command; a subsequently prepared command waits if the queue fills.
    wire offered=!src_reset && initialized_sync[1] && src_state==IDLE && (rd || we);
    wire [TXB:0] projected_writes={1'b0,pending_writes}+
        ((offered && we && !invalid_command) ? {count,1'b0}:9'b0);
    assign busy=src_reset || !initialized_sync[1] || src_state!=IDLE || cmd_full ||
        pending_commands>=((1<<FIFO_AW)-1) || projected_writes>MAX_WRITE_WORDS;
    assign fault=input_fault || backend_fault_sync[1];
    wire accept=offered && !busy;
    wire cmd_push=accept && !invalid_command;
    wire fifo_pop=!src_reset && src_state==READ && !fifo_empty;
    assign dout=bad_response ? 64'b0 : fifo_data;
    reg [PB-1:0] consumed_pairs=0, consumed_gray=0;
    wire [PB-1:0] next_consumed=consumed_pairs+1'b1;
    always @(posedge src_clk or posedge src_reset) begin
        if(src_reset) begin
            src_state<=IDLE; initialized_sync<=0; ready<=0; bad_response<=0;
            input_fault<=0; backend_fault_sync<=0; source_online<=0; src_remaining<=0;
            consumed_pairs<=0; consumed_gray<=0; command_sequence<=0; posted_words<=0;
            retired_command_meta<=0; retired_command_sync<=0; finished_word_meta<=0; finished_word_sync<=0;
        end else begin
            source_online<=1;
            initialized_sync<={initialized_sync[0],mem_initialized && !mem_reset};
            backend_fault_sync<={backend_fault_sync[0],backend_fault};
            retired_command_meta<=retired_command_gray; retired_command_sync<=retired_command_meta;
            finished_word_meta<=finished_word_gray; finished_word_sync<=finished_word_meta;
            ready<=0; bad_response<=0;
            if(fifo_pop) begin
                consumed_pairs<=next_consumed; consumed_gray<=(next_consumed>>1)^next_consumed;
            end
            if(accept && invalid_command) begin
                input_fault<=1; bad_read<=rd; src_remaining<=count; src_state<=BAD;
            end else if(cmd_push) begin
                command_sequence<=command_sequence+1'b1;
                if(we) posted_words<=posted_words+{count,1'b0};
                else begin src_remaining<=count; src_state<=READ; end
            end
            if(src_state==READ && fifo_pop) begin
                ready<=1; src_remaining<=src_remaining-1'b1;
                if(src_remaining==1) src_state<=IDLE;
            end
            if(src_state==BAD) begin
                if(bad_read && src_remaining!=0) begin
                    ready<=1; bad_response<=1; src_remaining<=src_remaining-1'b1;
                    if(src_remaining==1) src_state<=IDLE;
                end else src_state<=IDLE;
            end
        end
    end

    (* async_reg="true" *) reg [PB-1:0] consumed_meta=0, consumed_sync=0;
    reg [WB-1:0] issued_sequence=0;
    wire [WB-1:0] retired_words={pair_binary(consumed_sync),1'b0};
    wire [WB-1:0] reserved_words=issued_sequence-retired_words;
    reg running=0, command_write=0, prefetch_pending=0, prefetch_valid=0;
    reg [104:0] prefetch_data;
    reg [7:0] command_count=0, command_be=0;
    reg [23:0] command_address=0;
    reg [63:0] command_data=0;
    reg [8:0] issued=0, received=0;
    reg half_received=0;
    reg [31:0] low_word=0;
    wire [8:0] total_words={command_count,1'b0};
    assign mem_valid=!mem_reset && running && issued<total_words &&
        (command_write || reserved_words<CREDIT_WORDS);
    assign mem_write=command_write;
    assign mem_addr=command_address+{{15{1'b0}},issued};
    assign mem_wdata=issued[0] ? command_data[63:32] : command_data[31:0];
    assign mem_byte_en=issued[0] ? command_be[7:4] : command_be[3:0];
    wire take=mem_valid && mem_ready;
    wire read_return=!mem_reset && running && !command_write && mem_rvalid;
    wire fifo_push=read_return && half_received;
    wire command_finished=(take && command_write && issued==total_words-1'b1) ||
        (read_return && received==total_words-1'b1);
    wire cmd_pop=!mem_reset && mem_initialized && source_online_sync[1] && !prefetch_valid && !prefetch_pending && !cmd_empty;
    dual_clock_fifo #(.ADDR_WIDTH(FIFO_AW),.DATA_WIDTH(105)) commands (
        .wr_rst_i(src_reset),.wr_clk_i(src_clk),.wr_en_i(cmd_push),
        .wr_data_i({we,count,addr[25:2],be,din}),.full_o(cmd_full),
        .rd_rst_i(mem_reset || !source_online_sync[1]),.rd_clk_i(mem_clk),.rd_en_i(cmd_pop),
        .rd_data_o(cmd_fifo_data),.empty_o(cmd_empty));
    dual_clock_fifo #(.ADDR_WIDTH(FIFO_AW),.DATA_WIDTH(64)) responses (
        .wr_rst_i(mem_reset || !source_online_sync[1]),.wr_clk_i(mem_clk),.wr_en_i(fifo_push),
        .wr_data_i({mem_rdata,low_word}),.full_o(fifo_full),
        .rd_rst_i(src_reset),.rd_clk_i(src_clk),.rd_en_i(fifo_pop),
        .rd_data_o(fifo_data),.empty_o(fifo_empty));
    always @(posedge mem_clk or posedge mem_reset) begin
        if(mem_reset) begin
            consumed_meta<=0; consumed_sync<=0; source_online_sync<=0; issued_sequence<=0; running<=0; backend_fault<=0;
            issued<=0; received<=0; half_received<=0; prefetch_pending<=0; prefetch_valid<=0;
            retired_commands<=0; retired_command_gray<=0; finished_words<=0; finished_word_gray<=0;
        end else begin
            source_online_sync<={source_online_sync[0],source_online};
            consumed_meta<=consumed_gray; consumed_sync<=consumed_meta;
            prefetch_pending<=cmd_pop;
            if(cmd_pop) begin
                retired_commands<=retired_commands+1'b1;
                retired_command_gray<=((retired_commands+1'b1)>>1)^(retired_commands+1'b1);
            end
            if(prefetch_pending) begin prefetch_data<=cmd_fifo_data; prefetch_valid<=1; end
            if((!running || command_finished) && prefetch_valid) begin
                {command_write,command_count,command_address,command_be,command_data}<=prefetch_data;
                issued<=0; received<=0; half_received<=0; running<=1; prefetch_valid<=0;
            end else begin
                if(take) issued<=issued+1'b1;
                if(read_return) begin received<=received+1'b1; half_received<=!half_received; end
                if(command_finished) running<=0;
            end
            if(take && !command_write) issued_sequence<=issued_sequence+1'b1;
            if(take && command_write) begin
                finished_words<=finished_words+1'b1;
                finished_word_gray<=((finished_words+1'b1)>>1)^(finished_words+1'b1);
            end
            if(read_return && !half_received) low_word<=mem_rdata;
            if(mem_rvalid && (!running || command_write)) backend_fault<=1;
            if(fifo_push && fifo_full) backend_fault<=1;
        end
    end
`ifndef SYNTHESIS
    initial if(FIFO_AW<2 || FIFO_AW>6 || MAX_WRITE_WORDS<512 ||
        (1<<TXB)!=MAX_WRITE_WORDS*2) $fatal(1,"Invalid VRAM queue size");
    always @(posedge mem_clk) if(!mem_reset) begin
        if(fifo_push && fifo_full) $fatal(1,"VRAM response FIFO overflow");
        if(mem_rvalid && (!running || command_write)) $fatal(1,"Unexpected VRAM read return");
    end
    always @(posedge src_clk) if(!src_reset) begin
        if(cmd_push && cmd_full) $fatal(1,"VRAM command FIFO overflow");
        if(pending_writes>MAX_WRITE_WORDS) $fatal(1,"VRAM queued-write budget exceeded");
    end
`endif
endmodule
`default_nettype wire
