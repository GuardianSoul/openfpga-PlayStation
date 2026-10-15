// SPDX-License-Identifier: MIT
// Stretch the consumer reset until accepted main-memory work has drained.
// source_idle is in 1x; service_idle is sampled directly in 3x. A fresh
// service token is launched only after every source mailbox launch is seen.
module psx_main_reset_fence (
    input wire clk1x, clk3x, reset_async,
    input wire reset_request, source_idle, service_idle,
    output wire hold_reset
);
    (* async_reg="true" *) reg [2:0] reset1=7, reset3=7;
    always @(posedge clk1x or posedge reset_async)
        if(reset_async) reset1<=7; else reset1<={reset1[1:0],1'b0};
    always @(posedge clk3x or posedge reset_async)
        if(reset_async) reset3<=7; else reset3<={reset3[1:0],1'b0};

    localparam IDLE=0, WAIT_SOURCE=1, WAIT_SERVICE=2;
    reg [1:0] phase=IDLE;
    reg request_token=0, acknowledge_token=0;
    (* async_reg="true" *) reg [1:0] request_sync=0, acknowledge_sync=0;
    assign hold_reset=reset_request || phase!=IDLE;

    always @(posedge clk1x or posedge reset_async) begin
        if(reset_async) begin
            phase<=IDLE; request_token<=0; acknowledge_sync<=0;
        end else if(reset1[2]) begin
            phase<=IDLE; request_token<=0; acknowledge_sync<=0;
        end else begin
            acknowledge_sync<={acknowledge_sync[0],acknowledge_token};
            case(phase)
                IDLE: if(reset_request) phase<=WAIT_SOURCE;
                // One full consumer edge has elapsed with reset asserted.
                // A last launch on that edge must first be acknowledged by
                // 3x. Only then can a later fence token certify its drain.
                WAIT_SOURCE: if(source_idle) begin
                    request_token<=!request_token;
                    phase<=WAIT_SERVICE;
                end
                // Requests during a held reset coalesce. Never toggle twice
                // before acknowledgement and accidentally reuse an old ACK.
                WAIT_SERVICE: if(!reset_request && source_idle &&
                    acknowledge_sync[1]==request_token) phase<=IDLE;
                default: phase<=WAIT_SOURCE;
            endcase
        end
    end

    always @(posedge clk3x or posedge reset_async) begin
        if(reset_async) begin request_sync<=0; acknowledge_token<=0; end
        else if(reset3[2]) begin request_sync<=0; acknowledge_token<=0; end
        else begin
            request_sync<={request_sync[0],request_token};
            if(request_sync[1]!=acknowledge_token && service_idle)
                acknowledge_token<=request_sync[1];
        end
    end
endmodule
