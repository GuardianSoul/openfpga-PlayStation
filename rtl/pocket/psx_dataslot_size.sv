// SPDX-License-Identifier: MIT
// Host-clock APF ID/size table validation/update. Table positions are not IDs.
// request is captured even while table_grant=0; active lookup/write phases
// advance only with a reserved A-port grant. Q may have two read registers.
// Table population/reload must finish before request; IDs must be unique.
module psx_dataslot_size (
    input wire clk,reset,request,commit_size,table_grant,
    input wire [15:0] slot_id,input wire [31:0] size_bytes,expected_size,
    output wire busy,output reg done=0,failed=0,
    output wire [9:0] datatable_addr,output wire datatable_wren,
    output wire [31:0] datatable_data,input wire [31:0] datatable_q
);
    localparam IDLE=0,READ1=1,READ2=2,CHECK_ID=3,SIZE1=4,SIZE2=5,
        CHECK_SIZE=6,WRITE_SIZE=7,COMMIT=8;
    reg [3:0] state=IDLE;
    reg [4:0] index=0;
    reg [15:0] wanted_id=0;
    reg [31:0] wanted_size=0,old_size=0;
    reg update_size=0,prev_request=0;
    wire size_address=state==SIZE1 || state==SIZE2 || state==CHECK_SIZE || state==WRITE_SIZE || state==COMMIT;
    assign busy=state!=IDLE;
    assign datatable_addr={4'b0,index,size_address};
    assign datatable_wren=state==WRITE_SIZE && table_grant;
    assign datatable_data=wanted_size;
    always @(posedge clk) begin
        done<=0;prev_request<=request;
        if(reset) begin
            state<=IDLE;index<=0;wanted_id<=0;wanted_size<=0;old_size<=0;update_size<=0;
            done<=0;failed<=0;prev_request<=0;
        end else if(state==IDLE) begin
            if(request && !prev_request) begin
                wanted_id<=slot_id;wanted_size<=size_bytes;old_size<=expected_size;
                update_size<=commit_size;index<=0;failed<=0;state<=READ1;
            end
        end else if(table_grant) case(state)
            READ1: state<=READ2;
            READ2: state<=CHECK_ID;
            CHECK_ID: if(datatable_q[15:0]==wanted_id) begin
                    // Card length is 32-bit; stale upper length bits must not
                    // claim a file above 4 GiB after changing its lower size.
                    if(datatable_q[31:16]!=0) begin failed<=1;done<=1;state<=IDLE;end
                    else state<=SIZE1;
                end else if(index==31) begin failed<=1;done<=1;state<=IDLE;end
                else begin index<=index+1'b1;state<=READ1;end
            SIZE1: state<=SIZE2;
            SIZE2: state<=CHECK_SIZE;
            CHECK_SIZE: if(datatable_q!=old_size) begin failed<=1;done<=1;state<=IDLE;end
                else if(update_size) state<=WRITE_SIZE;
                else begin done<=1;state<=IDLE;end
            WRITE_SIZE: state<=COMMIT;
            COMMIT: begin done<=1;state<=IDLE;end
            default: begin failed<=1;done<=1;state<=IDLE;end
        endcase
    end
endmodule
