// SPDX-License-Identifier: MIT
// Ordered, SDRAM-backed geometry metadata. One 8-byte record per guest word:
// {22'b0, valid_y, valid_x, fy[3:0], fx[3:0], original_word[31:0]}.
// Main RAM uses word indices 0..524287; scratchpad uses 524288..524543.
// The filter is conservative: once allocated, a line remains allocated until
// invalidation. Its 16 records are cleared BEFORE its bit becomes visible.
// Clients must submit every ordered guest write, including DMA and partial
// writes, and must check/read the actual guest value with the specified mask.
`default_nettype none
module psx_pgxp_shadow #(
    parameter [24:0] SHADOW_BASE = 25'h1800000
) (
    input wire clk, reset, invalidate, enabled,
    output wire idle, initializing,
    input wire req_valid,
    output wire req_ready,
    input wire req_write,
    input wire [19:0] req_word,
    input wire [31:0] req_data,
    input wire [3:0] req_mask,
    input wire [9:0] req_metadata,
    output reg rsp_valid = 0,
    input wire rsp_ready,
    output reg [9:0] rsp_metadata = 0,
    output reg mem_valid = 0,
    input wire mem_ready,
    output reg mem_write = 0,
    output reg [24:0] mem_address = 0,
    output reg [63:0] mem_data = 0,
    input wire mem_done,
    input wire [63:0] mem_q,
    // Ordered read clients can snapshot before their guest data returns,
    // then validate this record against that same transaction's data.
    output reg [63:0] rsp_record = 0
);
    localparam S_CLEAR=0, S_IDLE=1, S_FILTER_DELAY=2, S_FILTER=3,
               S_SEND=4, S_WAIT=5;
    localparam A_READ=0, A_READ_WRITE=1, A_WRITE=2, A_ALLOCATE=3;
    reg [2:0] state = S_CLEAR;
    reg [1:0] action = A_READ;
    (* ramstyle = "M10K, no_rw_check" *) reg allocated [0:32767];
    reg filter_q;
    reg [14:0] clear_index = 0;
    reg [15:0] scratch_allocated = 0;
    reg [19:0] word_q = 0;
    reg write_q = 0;
    reg [31:0] data_q = 0;
    reg [3:0] mask_q = 0;
    reg [9:0] metadata_q = 0;
    reg [3:0] allocate_index = 0;
    wire scratch = word_q[19];
    wire line_present = scratch ? scratch_allocated[word_q[7:4]] : filter_q;
    wire tracking_write = (metadata_q[8] && &mask_q[1:0]) ||
                          (metadata_q[9] && &mask_q[3:2]);
    assign initializing = state == S_CLEAR;
    assign idle = state == S_IDLE && !rsp_valid;
    assign req_ready = idle && !invalidate && !reset;
    always @(posedge clk) filter_q <= allocated[word_q[18:4]];

    function [9:0] guarded;
        input [63:0] record_value;
        input [31:0] observed;
        input [3:0] observed_mask;
        reg vx, vy;
        begin
            vx=record_value[40] && &observed_mask[1:0] && record_value[15:0]==observed[15:0];
            vy=record_value[41] && &observed_mask[3:2] && record_value[31:16]==observed[31:16];
            guarded={vy,vx,vy ? record_value[39:36] : 4'd0,
                          vx ? record_value[35:32] : 4'd0};
        end
    endfunction
    function [63:0] merged;
        input [63:0] previous;
        input [31:0] value;
        input [3:0] byte_mask;
        input [9:0] metadata;
        reg [63:0] result;
        integer byte_index;
        begin
            result=previous;
            result[63:42]=0;
            for(byte_index=0;byte_index<4;byte_index=byte_index+1)
                if(byte_mask[byte_index]) result[byte_index*8+:8]=value[byte_index*8+:8];
            if(|byte_mask[1:0]) begin
                result[40]=(&byte_mask[1:0]) && metadata[8];
                result[35:32]=result[40] ? metadata[3:0] : 4'd0;
            end
            if(|byte_mask[3:2]) begin
                result[41]=(&byte_mask[3:2]) && metadata[9];
                result[39:36]=result[41] ? metadata[7:4] : 4'd0;
            end
            merged=result;
        end
    endfunction
    wire [24:0] record_address = SHADOW_BASE + {2'd0,word_q,3'd0};
    always @(posedge clk) begin
        if (reset || invalidate) begin
            state<=S_CLEAR; clear_index<=0; scratch_allocated<=0;
            rsp_valid<=0; rsp_metadata<=0; rsp_record<=0; mem_valid<=0;
        end else begin
            if(rsp_valid && rsp_ready) rsp_valid<=0;
            case(state)
                S_CLEAR: begin
                    allocated[clear_index]<=0;
                    clear_index<=clear_index+1'b1;
                    if(&clear_index) state<=S_IDLE;
                end
                S_IDLE: if(req_valid && req_ready) begin
                    // synthesis translate_off
                    assert(req_word<20'd524544) else $fatal(1,"PGXP shadow word outside RAM/scratchpad");
                    // synthesis translate_on
                    rsp_metadata<=0; rsp_record<=0;
                    if(!enabled) rsp_valid<=1;
                    else begin
                        word_q<=req_word; data_q<=req_data; mask_q<=req_mask;
                        metadata_q<=req_metadata; write_q<=req_write;
                        state<=S_FILTER_DELAY;
                    end
                end
                S_FILTER_DELAY: state<=S_FILTER;
                S_FILTER: begin
                    if(!line_present && (!write_q || !tracking_write)) begin
                        rsp_metadata<=0; rsp_valid<=1; state<=S_IDLE;
                    end else begin
                        mem_valid<=1; state<=S_SEND;
                        if(!line_present) begin
                            allocate_index<=0; action<=A_ALLOCATE;
                            mem_address<=SHADOW_BASE+{2'd0,word_q[19:4],7'd0};
                            mem_data<=0; mem_write<=1;
                        end else begin
                            mem_address<=record_address; mem_write<=0;
                            action<=write_q ? A_READ_WRITE : A_READ;
                        end
                    end
                end
                S_SEND: if(mem_valid && mem_ready) begin
                    mem_valid<=0; state<=S_WAIT;
                end
                S_WAIT: if(mem_done) begin
                    case(action)
                        A_READ: begin
                            rsp_record<=mem_q;
                            rsp_metadata<=guarded(mem_q,data_q,mask_q);
                            rsp_valid<=1; state<=S_IDLE;
                        end
                        A_READ_WRITE: begin
                            mem_data<=merged(mem_q,data_q,mask_q,metadata_q);
                            mem_write<=1; mem_valid<=1; action<=A_WRITE; state<=S_SEND;
                        end
                        A_WRITE: begin rsp_metadata<=0; rsp_valid<=1; state<=S_IDLE; end
                        A_ALLOCATE: begin
                            mem_valid<=1; state<=S_SEND;
                            if(&allocate_index) begin
                                if(scratch) scratch_allocated[word_q[7:4]]<=1;
                                else allocated[word_q[18:4]]<=1;
                                mem_address<=record_address;
                                mem_data<=merged(64'd0,data_q,mask_q,metadata_q);
                                action<=A_WRITE;
                            end else begin
                                allocate_index<=allocate_index+1'b1;
                                mem_address<=mem_address+25'd8;
                            end
                        end
                    endcase
                end
                default: state<=S_CLEAR;
            endcase
        end
    end
endmodule
`default_nettype wire
