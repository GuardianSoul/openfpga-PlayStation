// SPDX-License-Identifier: MIT
// Commit the runtime setting only between geometry/DMA/memory operations.
// Every transition clears provenance, then waits for the complete filter
// sweep and a new quiescent boundary before enabling enhanced geometry.
`default_nettype none
module psx_pgxp_mode (
    input wire clk,reset,requested,quiescent,initializing,
    output reg enabled=0,
    output reg clear=0
);
    (* async_reg="true" *) reg [1:0] request_sync=0;
    localparam STABLE=0,CLEAR_WAIT=1,INITIALIZE=2;
    reg [1:0] state=INITIALIZE;
    always @(posedge clk)begin
        if(reset)request_sync<=0;
        else request_sync<={request_sync[0],requested};
        clear<=0;
        if(reset)begin enabled<=0;clear<=1;state<=CLEAR_WAIT;end
        else case(state)
            STABLE: if(request_sync[1]!=enabled && quiescent)begin
                enabled<=0;clear<=1;state<=CLEAR_WAIT;
            end
            CLEAR_WAIT:state<=INITIALIZE;
            INITIALIZE:if(!initializing && quiescent)begin
                enabled<=request_sync[1];state<=STABLE;
            end
            default:begin enabled<=0;clear<=1;state<=CLEAR_WAIT;end
        endcase
    end
endmodule
`default_nettype wire
