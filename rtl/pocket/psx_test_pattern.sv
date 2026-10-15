// SPDX-License-Identifier: MIT
module psx_test_pattern (
    input wire clk, reset,
    input wire passed, failed, checking,
    input wire [17:0] progress,
    output wire [23:0] rgb,
    output wire de, hs, vs
);
    reg [9:0] x=0, y=0;
    (* async_reg="true" *) reg [2:0] status_meta=0, status_sync=0;
    always @(posedge clk) begin
        status_meta<={failed,passed,checking}; status_sync<=status_meta;
    end
    always @(posedge clk) begin
        if (reset) begin x<=0; y<=0; end
        else if (x==799) begin x<=0; y<=y==524 ? 0 : y+1'b1; end
        else x<=x+1'b1;
    end
    // 640x480, 800x525 totals, positive APF frame/line pulses.
    assign de=x<640 && y<480;
    assign hs=x>=656 && x<752;
    assign vs=y>=490 && y<492;
    wire [23:0] status=status_sync[2] ? 24'hff2020 : status_sync[1] ? 24'h20ff40 : status_sync[0] ? 24'hffff20 : 24'h2080ff;
    wire [23:0] bars={x[6] ? 8'hff:8'h00,x[7] ? 8'hff:8'h00,x[8] ? 8'hff:8'h00};
    // The status rectangle is the authoritative result. A free-running counter
    // in the video domain supplies animation without sampling a binary counter
    // from another domain.
    reg [7:0] animation=0;
    always @(posedge clk) if (x==0 && y==0) animation<=animation+1'b1;
    assign rgb=!de ? 24'b0 : y<64 ? status : y>=448 ? {animation,animation,animation} : bars;
endmodule
