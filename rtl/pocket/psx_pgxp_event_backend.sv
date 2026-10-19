// SPDX-License-Identifier: MIT
// Preserve the controller's total order across writes and read snapshots.
// Event layout: {5'b0,kind[1:0],cpu_tag[7:0],meta[9:0],mask[3:0],word[18:0],data[31:0]}.
// kind: 0=write, 1=CPU read, 2=GPU read, 3=GPU native fallback.
`default_nettype none
module psx_pgxp_event_backend (
    input wire clk, reset, invalidate, enabled,
    input wire event_valid,
    output wire event_ready,
    input wire [79:0] event_data,
    output wire cpu_valid,
    input wire cpu_ready,
    output wire [7:0] cpu_tag,
    output wire [63:0] cpu_record,
    output wire gpu_valid,
    input wire gpu_ready,
    output wire [63:0] gpu_record,
    output wire idle, initializing,
    output wire mem_valid,
    input wire mem_ready,
    output wire mem_write,
    output wire [24:0] mem_address,
    output wire [63:0] mem_data,
    input wire mem_done,
    input wire [63:0] mem_q
);
    localparam E_IDLE=0,E_SHADOW=1,E_FALLBACK=2;
    reg [1:0] state=E_IDLE,owner=0;
    reg [7:0] tag_q=0;
    wire req_ready,rsp_valid,rsp_ready,shadow_idle;
    wire [63:0] record_q;
    wire [1:0] kind=event_data[74:73];
    assign event_ready=enabled && state==E_IDLE && (kind==3 || req_ready);
    wire request=event_valid && enabled && state==E_IDLE && kind!=3;
    assign rsp_ready=owner==0 ? 1'b1 : owner==1 ? cpu_ready : gpu_ready;
    assign cpu_valid=state==E_SHADOW && owner==1 && rsp_valid;
    assign cpu_tag=tag_q;
    assign cpu_record=record_q;
    assign gpu_valid=(state==E_SHADOW && owner==2 && rsp_valid) || state==E_FALLBACK;
    assign gpu_record=state==E_FALLBACK ? 64'd0 : record_q;
    assign idle=state==E_IDLE && shadow_idle && !event_valid;
    psx_pgxp_shadow shadow (
        .clk(clk),.reset(reset),.invalidate(invalidate),.enabled(enabled),
        .idle(shadow_idle),.initializing(initializing),
        .req_valid(request),.req_ready(req_ready),.req_write(kind==0),
        .req_word({1'b0,event_data[50:32]}),.req_data(event_data[31:0]),
        .req_mask(event_data[54:51]),.req_metadata(event_data[64:55]),
        .rsp_valid(rsp_valid),.rsp_ready(rsp_ready),.rsp_metadata(),.rsp_record(record_q),
        .mem_valid(mem_valid),.mem_ready(mem_ready),.mem_write(mem_write),
        .mem_address(mem_address),.mem_data(mem_data),.mem_done(mem_done),.mem_q(mem_q)
    );
    always @(posedge clk)begin
        if(reset || invalidate)begin state<=E_IDLE;owner<=0;tag_q<=0;end
        else case(state)
            E_IDLE: if(event_valid && event_ready)begin
                owner<=kind;tag_q<=event_data[72:65];
                state<=kind==3 ? E_FALLBACK : E_SHADOW;
            end
            E_SHADOW: if(rsp_valid && rsp_ready)state<=E_IDLE;
            E_FALLBACK: if(gpu_ready)state<=E_IDLE;
            default:state<=E_IDLE;
        endcase
    end
endmodule
`default_nettype wire
