// SPDX-License-Identifier: MIT
// Own save-entry observer. No guest backpressure or guest-memory writes.
// 32 header words + 768 records of eight words (six payload, two reserved).
// pre[0:127]: rolling before request; first[128:383]: first handler events;
// recent[384:767]: rolling after request, including stalled heartbeats.
// CPU samples describe the EXECUTE stage, not instruction retirement.
// The APF nonvolatile slot exports this RAM on core exit, independently of CPU.
`default_nettype none
module psx_save_trace #(
    parameter WAIT_BITS=29, WINDOW_BITS=24, HEARTBEAT_BITS=12
) (
    input wire clk, reset,
    input wire [31:0] pc, opcode, flags, epc, bus_addr, bus_data,
    input wire [31:0] keys, sticks,
    input wire read_clk, read_reset,
    input wire request,
    input wire export_read,
    input wire [12:0] read_index,
    input wire [31:0] host_events,
    output reg [31:0] read_data
);
    localparam [31:0] MAGIC=32'h474c5353; // SSLG, little endian
    (* ramstyle="M10K" *) reg [191:0] records [0:767];
    reg request_d=0, request_toggle=0, export_level=0;
    reg [7:0] requests=0;
    always @(posedge read_clk) begin
        if (read_reset) begin
            request_d<=0; request_toggle<=0; export_level<=0; requests<=0;
        end else begin
            request_d<=request;
            if (request && !request_d) begin request_toggle<=!request_toggle; if (requests!=8'hff) requests<=requests+1'b1; end
            if (export_read) export_level<=1;
        end
    end
    (* async_reg="true" *) reg req_meta=0, req_sync=0, export_meta=0, export_sync=0;
    reg req_seen=0;
    reg triggered=0, entered=0, returned=0, resumed=0, frozen=0, export_stop=0;
    reg [31:0] cycles=0, trigger_cycle=0, entry_cycle=0, stop_cycle=0;
    reg [WAIT_BITS:0] age=0;
    reg [7:0] pre_count=0;
    reg [6:0] pre_next=0;
    reg [8:0] first_count=0, late_count=0, late_next=0;
    reg [31:0] entry_epc=0;
    reg [31:0] trigger_keys=0, trigger_sticks=0;
    wire step=flags[10] && !flags[11] && flags[9:0]==0;
    wire heartbeat=cycles[HEARTBEAT_BITS-1:0]==0;
    wire event_valid=step || flags[19] || heartbeat;
    wire in_handler=pc[31:13]==19'h5f800;
    wire entry_event=triggered && !entered && step && in_handler;
    wire [191:0] sample={bus_data,bus_addr,flags,opcode,pc,cycles};
    // One RAM write port. Before entry, recent retains the wait; once entry
    // starts, first has priority until full, then recent resumes.
    always @(posedge clk) begin
        req_meta<=request_toggle; req_sync<=req_meta;
        export_meta<=export_level; export_sync<=export_meta;
        if (reset) begin
            req_meta<=0; req_sync<=0; export_meta<=0; export_sync<=0; req_seen<=0;
            triggered<=0; entered<=0; returned<=0; resumed<=0; frozen<=0; export_stop<=0;
            cycles<=0; pre_count<=0; pre_next<=0; first_count<=0; late_count<=0; late_next<=0;
            age<=0;
            trigger_cycle<=0; entry_cycle<=0; stop_cycle<=0;
            entry_epc<=0;
            trigger_keys<=0; trigger_sticks<=0;
        end else if (!frozen) begin
            cycles<=cycles+1'b1;
            if (req_sync!=req_seen) begin
                req_seen<=req_sync;
                if (!triggered) begin
                    triggered<=1; trigger_cycle<=cycles;
                    trigger_keys<=keys; trigger_sticks<=sticks;
                end
            end
            if (triggered) begin
                age<=age+1'b1;
            end
            if (entry_event) begin entered<=1; entry_cycle<=cycles; entry_epc<=epc; age<=0; end
            if (entered && step && in_handler && opcode==32'h42000010) begin
                returned<=1;
            end
            if (returned && !resumed && step && !in_handler) begin resumed<=1; end
            if (export_sync || (entered && age[WINDOW_BITS]) || (!entered && age[WAIT_BITS])) begin
                frozen<=1; export_stop<=export_sync; stop_cycle<=cycles;
            end else if (event_valid) begin
                if (!triggered) begin
                    records[{3'b0,pre_next}]<=sample;
                    pre_next<=pre_next+1'b1;
                    if (pre_count<128) pre_count<=pre_count+1'b1;
                end else if ((entered || entry_event) && first_count<256) begin
                    records[10'd128+{1'b0,first_count}]<=sample;
                    first_count<=first_count+1'b1;
                end else begin
                    records[10'd384+{1'b0,late_next}]<=sample;
                    late_next<=late_next==383 ? 0 : late_next+1'b1;
                    if (late_count<384) late_count<=late_count+1'b1;
                end
            end
        end
    end
    wire [1023:0] header={
        32'd0,32'd0,32'd0,32'd0,32'd0,32'd0,32'd0,32'd0,32'd0,
        trigger_sticks,32'd0,32'd0,32'd0,trigger_keys,32'd0,32'd0,32'd0,entry_epc,
        32'd0,32'd0,stop_cycle,trigger_cycle,
        26'd0,export_stop,resumed,returned,entered,triggered,frozen,
        23'd0,late_next,23'd0,late_count,23'd0,first_count,25'd0,pre_next,24'd0,pre_count,
        128'd0}; // fixed header words are selected directly below
    (* async_reg="true" *) reg frozen_meta=0, frozen_sync=0;
    reg [31:0] header_q=0;
    reg [191:0] q=0;
    reg [12:0] index_d=0;
    wire [9:0] record_index=read_index[12:3]-10'd4;
    always @(posedge read_clk) begin
        frozen_meta<=frozen; frozen_sync<=frozen_meta;
        if (read_reset) begin
            frozen_meta<=0; frozen_sync<=0; header_q<=0;
        end else begin
            header_q<=frozen_sync ? header[read_index[4:0]*32 +: 32] : 32'd0;
        end
        q<=records[record_index]; index_d<=read_index;
    end
    always @* begin
        if (index_d==0) read_data=MAGIC;
        else if (index_d==1) read_data=5;
        else if (index_d==2) read_data=768;
        else if (index_d==3) read_data=8;
        else if (index_d==21) read_data=requests;
        else if (index_d==31) read_data=host_events;
        else if (index_d<32) read_data=header_q;
        else case (index_d[2:0])
            3'd0: read_data=q[31:0];
            3'd1: read_data=q[63:32];
            3'd2: read_data=q[95:64];
            3'd3: read_data=q[127:96];
            3'd4: read_data=q[159:128];
            3'd5: read_data=q[191:160];
            default: read_data=0;
        endcase
    end
endmodule
`default_nettype wire
