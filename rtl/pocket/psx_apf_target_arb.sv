// SPDX-License-Identifier: MIT
// Host-clock sharing of APF target read/write commands. One active/queued
// operation per client. Hold descriptors while valid until ready. A held
// assertion is accepted once; deassert valid before the next request.
// p_done is client-specific, sticky, and cleared at request acceptance.
// reset is FPGA/power reset ONLY. Guest/client reset must not revoke an
// active host command; its client waits for ready before issuing again.
module psx_apf_target_arb #(parameter integer CLIENTS=2) (
    input wire clk,reset,
    input wire [CLIENTS-1:0] p_valid,p_write,
    output wire [CLIENTS-1:0] p_ready,p_busy,
    input wire [CLIENTS-1:0][15:0] p_id,
    input wire [CLIENTS-1:0][31:0] p_offset,p_bridge,p_length,
    output reg [CLIENTS-1:0] p_done,p_issued,
    output reg [CLIENTS-1:0][2:0] p_error,
    output reg target_read=0,target_write=0,
    output reg [15:0] target_id=0,
    output reg [31:0] target_offset=0,target_bridge=0,target_length=0,
    input wire target_done,input wire [2:0] target_error,
    output reg protocol_error=0
);
    localparam integer IW=CLIENTS>1 ? $clog2(CLIENTS):1;
    localparam IDLE=0,LAUNCH=1,WAIT_RESULT=2;
    reg [1:0] state=IDLE;
    reg [IW-1:0] owner=0,next_client=0,selected=0;
    reg active=0,found=0,operation_write=0,done_low_seen=0;
    reg [CLIENTS-1:0] pending=0,served=0,waiting=0,queued_write=0;
    reg [CLIENTS-1:0][15:0] queued_id=0;
    reg [CLIENTS-1:0][31:0] queued_offset=0,queued_bridge=0,queued_length=0;
    wire [CLIENTS-1:0] active_mask=active ? ({{(CLIENTS-1){1'b0}},1'b1}<<owner):0;
    assign p_busy=pending | active_mask;
    assign p_ready={CLIENTS{!reset}} & ~(pending | active_mask | served);
    always @* begin
        selected=0;found=0;
        for(integer n=0;n<CLIENTS;n++) begin
            integer candidate;
            candidate=(integer'(next_client)+n)%CLIENTS;
            if(!found && pending[candidate]) begin selected=IW'(candidate);found=1;end
        end
    end
    always @(posedge clk) begin
        target_read<=0;target_write<=0;p_issued<=0;
        if(reset) begin
            state<=IDLE;owner<=0;next_client<=0;active<=0;operation_write<=0;done_low_seen<=0;
            pending<=0;served<=0;waiting<=0;queued_write<=0;queued_id<=0;
            queued_offset<=0;queued_bridge<=0;queued_length<=0;
            p_done<={CLIENTS{1'b1}};p_error<=0;p_issued<=0;protocol_error<=0;
            target_read<=0;target_write<=0;target_id<=0;target_offset<=0;target_bridge<=0;target_length<=0;
        end else begin
            for(integer i=0;i<CLIENTS;i++) begin
                if(!p_valid[i]) begin
                    served[i]<=0;
                    if(waiting[i]) protocol_error<=1;
                    waiting[i]<=0;
                end else if(p_ready[i]) begin
                    pending[i]<=1;served[i]<=1;waiting[i]<=0;p_done[i]<=0;p_error[i]<=0;
                    queued_write[i]<=p_write[i];queued_id[i]<=p_id[i];
                    queued_offset[i]<=p_offset[i];queued_bridge[i]<=p_bridge[i];queued_length[i]<=p_length[i];
                end else if(!served[i]) waiting[i]<=1;
            end
            case(state)
                IDLE: if(found) begin
                    owner<=selected;active<=1;pending[selected]<=0;
                    operation_write<=queued_write[selected];target_id<=queued_id[selected];
                    target_offset<=queued_offset[selected];target_bridge<=queued_bridge[selected];
                    target_length<=queued_length[selected];done_low_seen<=0;state<=LAUNCH;
                    next_client<=selected==CLIENTS-1 ? 0:selected+1'b1;
                end
                LAUNCH: begin
                    target_read<=!operation_write;target_write<=operation_write;
                    p_issued[owner]<=1;state<=WAIT_RESULT;
                end
                WAIT_RESULT: begin
                    // Real handler completion is sticky. A peer's earlier
                    // completion cannot finish this operation before its own
                    // command has cleared done and later reported a result.
                    if(!target_done) done_low_seen<=1;
                    if(done_low_seen && target_done) begin
                        p_done[owner]<=1;p_error[owner]<=target_error;
                        active<=0;state<=IDLE;
                    end
                end
                default: begin protocol_error<=1;state<=IDLE;active<=0;end
            endcase
        end
    end
endmodule
